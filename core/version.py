'''
版本管理核心模块 - 独立单Jar模式
修复：
1. Fabric Loader 严格遵循 meta.fabricmc.net 官方路由表
2. Fabric API 使用 Modrinth API（不再访问无效路径）
3. 所有 Fabric 相关接口支持 BMCLAPI 镜像 fallback
4. 彻底移除 /v2/versions/api 非法路径
5. 增加稳定版过滤、日志区分、错误提示
6. 修复 Mod 页主线程阻塞、空数据崩溃、网络异常未捕获问题
7. 新增 Modrinth 模组搜索异步 Worker，彻底解决空白 + 0xC0000409 崩溃
8. 所有 Worker 独立 DownloadHelper，杜绝跨线程 Session 共用
'''

import json
import os
import platform
import re
import shutil
import subprocess
import logging
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import (Optional, List, Callable, Any, Dict)
from PySide6.QtCore import (QThread, Signal, QObject)
import minecraft_launcher_lib
import requests
import certifi
from core.config import ConfigManager
from core.paths import safe_rmtree
from core.launch import LaunchManager
from core.installers import (InstallCancelled, create_alias, install_fabric, install_forge, install_neoforge, install_vanilla, retarget_launcher_profile)
from core.net import DownloadCancelled, HttpClient
from core.paths import scl_dir
logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger('VersionManager')
PROJECT_TEMP = scl_dir() / 'temp_install'
PROJECT_TEMP.mkdir(parents=True, exist_ok=True)
INCOMPLETE_MARKER = '.pmgl_incomplete'
DEPENDENCY_MARKER = '.pmgl_dependency'
_IS_64BIT = platform.architecture()[0] == '64bit'
_OS_NAME = 'windows' if platform.system() == 'Windows' else 'osx' if platform.system() == 'Darwin' else 'linux'
_ACTIVE_INSTALLS: Dict[str, tuple[str, set[str]]] = {}
_ACTIVE_INSTALL_LOCK = threading.RLock()

def _versions_key(versions_dir: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(versions_dir)))

def register_active_install(token: str, versions_dir: str | Path) -> set[str]:
    '''记录安装开始前的版本目录集合，返回该快照。'''

    versions_dir = Path(versions_dir)
    try:
        before = {item.name for item in versions_dir.iterdir() if item.is_dir()}
    except OSError:
        before = set()
    with _ACTIVE_INSTALL_LOCK:
        _ACTIVE_INSTALLS[str(token)] = _versions_key(versions_dir), before
    return before

def unregister_active_install(token: str) -> None:
    with _ACTIVE_INSTALL_LOCK:
        _ACTIVE_INSTALLS.pop(str(token), None)

def _active_install_hidden(versions_dir: str | Path) -> set[str]:
    '''返回进行中的安装任务新创建、尚不应展示的版本 ID。'''

    key = _versions_key(versions_dir)
    with _ACTIVE_INSTALL_LOCK:
        snapshots = [before for dir_key, before in _ACTIVE_INSTALLS.values() if dir_key == key]
    if not snapshots:
        return set()
    hidden = set()
    for before in snapshots:
        try:
            for item in Path(versions_dir).iterdir():
                if item.is_dir() and item.name not in before:
                    hidden.add(item.name)
        except OSError:
            continue
    return hidden

def detect_version_loader(version_id: str, data: Optional[dict]=None, version_dir: Optional[Path]=None) -> str:
    '''识别实例使用的加载器：forge / fabric / neoforge / quilt / optifine。'''

    if version_dir is not None:
        pack_file = Path(version_dir) / 'pmgl_pack.json'
        try:
            pack = json.loads(pack_file.read_text(encoding='utf-8'))
            loader = str(pack.get('loader') or '').strip().lower()
            if loader in ('forge', 'fabric', 'neoforge', 'quilt', 'optifine'):
                return loader
        except (OSError, ValueError):
            pass
    name = str(version_id or '').lower()
    if 'neoforge' in name:
        return 'neoforge'
    if 'forge' in name:
        return 'forge'
    if 'fabric' in name:
        return 'fabric'
    if 'quilt' in name:
        return 'quilt'
    if 'optifine' in name:
        return 'optifine'
    if not isinstance(data, dict):
        return ''
    try:
        blob = json.dumps(data, ensure_ascii=False).lower()
    except (TypeError, ValueError):
        pass
    else:
        if 'net.neoforged' in blob or 'neoforge' in blob:
            return 'neoforge'
        if 'net.minecraftforge' in blob or 'minecraftforge' in blob:
            return 'forge'
        if 'fabric-loader' in blob or 'net.fabricmc' in blob:
            return 'fabric'
        if 'quilt-loader' in blob or 'org.quiltmc' in blob:
            return 'quilt'
        if 'optifine' in blob:
            return 'optifine'
        return ''

class DownloadHelper:
    '''多镜像源下载辅助类（支持重试、fallback），每个实例独立 Session'''

    def __init__(self, mirrors: Optional[List[str]]=None, timeout: int=30, retries: int=3):
        self.timeout = timeout
        self.retries = retries
        self.mirrors = mirrors if mirrors else ConfigManager().get_mirror_list()
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'PMGL-Launcher/1.0', 'Accept-Language': 'zh-CN,zh;q=0.9', 'Connection': 'keep-alive'})
        self.session.max_redirects = 30
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry_strategy = Retry(total=retries, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=['GET'])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _build_full_url(self, path: str) -> str:
        if path.startswith('http'):
            return path
        base = self.mirrors[0].rstrip('/')
        clean_path = path.lstrip('/')
        return f'{base}/{clean_path}'

    def _fetch_raw(self, url: str, accept_json: bool=False, fallback_urls: Optional[List[str]]=None):
        urls_to_try = [self._build_full_url(url)]
        if fallback_urls:
            for fb in fallback_urls:
                fb_url = self._build_full_url(fb)
                if fb_url not in urls_to_try:
                    urls_to_try.append(fb_url)
        last_exception = None
        for attempt in range(self.retries):
            for attempt_url in urls_to_try:
                try:
                    logger.debug(f'请求: {attempt_url}')
                    resp = self.session.get(attempt_url, timeout=self.timeout, verify=certifi.where(), allow_redirects=True)
                    resp.raise_for_status()
                    if accept_json:
                        return resp.json()
                    return resp.content
                except (requests.RequestException, ConnectionError, TimeoutError) as e:
                    err_msg = str(e)
                    logger.warning(f'请求失败 {attempt_url} | {err_msg}')
                    last_exception = err_msg
                    time.sleep(1)
                    continue
            if attempt < self.retries - 1:
                logger.info(f'第{attempt + 1}轮全部失败，等待2秒重试')
                time.sleep(2)
        raise Exception(f'所有地址尝试均失败，最后一次错误: {last_exception}')

    def download_file(self, url: str, dest: Path, fallback_urls: Optional[List[str]]=None, progress_callback: Optional[Callable[[int, int], None]]=None) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if url.startswith('http'):
            final_url = url
        else:
            final_url = self._build_full_url(url)
        data = self._fetch_raw(final_url, accept_json=False, fallback_urls=fallback_urls)
        with open(dest, 'wb') as f:
            f.write(data)
        return True

    def fetch_json(self, url: str, fallback_urls: Optional[List[str]]=None) -> dict:
        if url.startswith('http'):
            return self._fetch_raw(url, accept_json=True, fallback_urls=fallback_urls)
        return self._fetch_raw(url, accept_json=True, fallback_urls=fallback_urls)

    def download_absolute(self, url: str, dest: Path, retries: Optional[int]=None) -> bool:
        if retries is None:
            retries = self.retries
        dest.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(retries):
            try:
                resp = self.session.get(url, timeout=self.timeout, verify=certifi.where())
                resp.raise_for_status()
                with open(dest, 'wb') as f:
                    f.write(resp.content)
                    return True
                return True
            except Exception as e:
                logger.warning(f'下载绝对URL失败 (尝试 {attempt + 1}/{retries}): {e}')
                if attempt < retries - 1:
                    time.sleep(2)
                continue
        return False


def scan_all_java() -> List[tuple]:
    launch_mgr = LaunchManager()
    java_list = launch_mgr._detect_all_java()
    candidates = []
    for ver_str, path in java_list:
        try:
            if ver_str.startswith('1.'):
                major = int(ver_str.split('.')[1])
            else:
                major = int(ver_str.split('.')[0])
            if major >= 25:
                candidates.append((major, path))
        except:
            pass
    unique = {}
    for major, path in candidates:
        if path not in unique or major > unique[path]:
            unique[path] = major
    result = [(major, path) for path, major in unique.items()]
    result.sort(key=(lambda x: x[0]), reverse=True)
    return result

class VersionManager:
    def __init__(self, mc_dir=None):
        self.mc_dir = mc_dir or ConfigManager().get_mc_dir()
        self.versions_dir = Path(self.mc_dir) / 'versions'
        self.config = ConfigManager()
        self.dl_helper = DownloadHelper(self.config.get_mirror_list())

    def _dependency_version_ids(self) -> set[str]:
        '''被其他实例继承的原版/加载器版本只作为依赖，不在列表中重复显示。'''

        dependency_ids = set()
        if not self.versions_dir.exists():
            return dependency_ids
        for ver_dir in self.versions_dir.iterdir():
            if not ver_dir.is_dir():
                continue
            current = ver_dir.name
            seen = {current}
            for _ in range(12):
                json_path = self.versions_dir / current / f'{current}.json'
                try:
                    data = json.loads(json_path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                else:
                    parent = str(data.get('inheritsFrom') or '')
                    if parent:
                        if parent in seen:
                            continue
                    parent_dir = self.versions_dir / parent
                    if not parent_dir.is_dir():
                        continue
                    dependency_ids.add(parent)
                    seen.add(parent)
                    current = parent
        return dependency_ids

    def get_installed_versions(self):
        versions = []
        if not self.versions_dir.exists():
            return versions
        dependency_ids = self._dependency_version_ids()
        active_hidden = _active_install_hidden(self.versions_dir)
        for ver_dir in self.versions_dir.iterdir():
            if ver_dir.is_dir():
                if ver_dir.name in active_hidden:
                    continue
                if (ver_dir / INCOMPLETE_MARKER).exists():
                    continue
                if (ver_dir / DEPENDENCY_MARKER).exists():
                    continue
                if ver_dir.name in dependency_ids:
                    continue
                expected_json = ver_dir / f'{ver_dir.name}.json'
                if expected_json.exists():
                    try:
                        with open(expected_json, encoding='utf-8') as f:
                            data = json.load(f)
                        loader = detect_version_loader(ver_dir.name, data, ver_dir)
                        if not loader:
                            hint = f"{data.get('inheritsFrom') or ''} {data.get('jar') or ''}".lower()
                            for key in ('neoforge', 'forge', 'fabric', 'quilt', 'optifine'):
                                if key in hint:
                                    loader = key
                                    break
                        vtype = str(data.get('type') or 'release')
                        if vtype.lower() == 'unknown':
                            vtype = 'release'
                        versions.append({'id': ver_dir.name, 'type': vtype, 'loader': loader})
                    except Exception:
                        pass
        return versions

    def is_version_installed(self, version_id):
        ver_dir = self.versions_dir / version_id
        return ver_dir.exists() and not (ver_dir / INCOMPLETE_MARKER).exists() and (ver_dir / f'{version_id}.json').exists()

    def get_modded_versions(self):
        '''返回已安装的非原版实例，按最近安装/修改时间倒序。'''

        result = []
        if not self.versions_dir.exists():
            return result
        dependency_ids = self._dependency_version_ids()
        active_hidden = _active_install_hidden(self.versions_dir)
        for ver_dir in self.versions_dir.iterdir():
            if not ver_dir.is_dir():
                continue
            if ver_dir.name in active_hidden:
                continue
            if (ver_dir / INCOMPLETE_MARKER).exists():
                continue
            if (ver_dir / DEPENDENCY_MARKER).exists():
                continue
            if ver_dir.name in dependency_ids:
                continue
            json_path = ver_dir / f'{ver_dir.name}.json'
            if not json_path.exists():
                continue
            name = ver_dir.name.lower()
            is_modded = any((k in name for k in ('forge', 'fabric', 'neoforge', 'quilt', 'optifine'))) or (ver_dir / 'pmgl_pack.json').exists() or (ver_dir / 'game').exists()
            if not is_modded:
                try:
                    blob = json.dumps(json.loads(json_path.read_text(encoding='utf-8')), ensure_ascii=False).lower()
                    is_modded = any((marker in blob for marker in ('fabric-loader', 'net.fabricmc', 'quilt-loader', 'org.quiltmc', 'minecraftforge', 'net.minecraftforge', 'bootstraplauncher', 'net.neoforged', 'neoforge', 'optifine')))
                except (OSError, ValueError):
                    is_modded = False
            if not is_modded:
                continue
            try:
                mtime = json_path.stat().st_mtime
            except OSError:
                mtime = 0
            result.append((mtime, ver_dir.name))
        result.sort(reverse=True)
        return [vid for _mtime, vid in result]

    @staticmethod
    def _library_applies(lib: dict) -> bool:
        rules = lib.get('rules')
        if not rules:
            return True
        os_name = _OS_NAME
        is_64 = _IS_64BIT
        allowed = False
        def matches(rule: dict) -> bool:
            for key, value in (rule.get('os') or {}).items():
                if key == 'name' and value != os_name:
                    return False
                if key == 'arch' and value == 'x86' and is_64:
                    return False
                if key == 'arch' and value in ('x86_64', 'amd64') and not is_64:
                    return False
            return True

        for rule in rules:
            if not matches(rule):
                continue
            if rule.get('action') == 'disallow':
                return False
            if rule.get('action') == 'allow':
                allowed = True
        return allowed

    def validate_version(self, version_id: str) -> dict:
        '''快速结构校验：返回 valid / fatal / issues。'''

        result = {'version_id': version_id, 'valid': True, 'fatal': False, 'issues': []}
        mc_root = Path(self.mc_dir)
        version_dir = self.versions_dir / version_id
        if not version_dir.is_dir():
            result.update(valid=False, fatal=True, issues=['版本文件夹不存在'])
            return result
        if (version_dir / INCOMPLETE_MARKER).exists():
            result.update(valid=False, fatal=True, issues=['版本安装未完成'])
            return result
        json_path = version_dir / f'{version_id}.json'
        if not json_path.is_file():
            result.update(valid=False, fatal=True, issues=['缺少版本 JSON'])
            return result
        try:
            data = json.loads(json_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            result.update(valid=False, fatal=True, issues=[f'版本 JSON 损坏：{exc}'])
            return result
        if not (isinstance(data, dict) and data.get('id')):
            result.update(valid=False, fatal=True, issues=['版本 JSON 内容无效'])
            return result
        if not (data.get('mainClass') or data.get('inheritsFrom')):
            result.update(valid=False, fatal=True, issues=['版本 JSON 缺少 mainClass'])
            return result
        chain = []
        current_id = version_id
        current_data = data
        seen = set()
        while isinstance(current_data, dict) and current_id not in seen:
            seen.add(current_id)
            chain.append((current_id, current_data))
            parent = current_data.get('inheritsFrom')
            if not parent:
                break
            parent_json = self.versions_dir / parent / f'{parent}.json'
            if not parent_json.is_file():
                result['issues'].append(f'缺少父版本 {parent}')
                break
            try:
                current_data = json.loads(parent_json.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                result['issues'].append(f'父版本 {parent} 的 JSON 损坏')
                break
            current_id = parent
        jar_id = None
        for dir_id, info in chain:
            if info.get('jar'):
                jar_id = info['jar']
                break
        if jar_id is None:
            for dir_id, _info in reversed(chain):
                if (self.versions_dir / dir_id / f'{dir_id}.jar').is_file():
                    jar_id = dir_id
                    break
        if jar_id is None:
            jar_id = version_id
        if not (self.versions_dir / jar_id / f'{jar_id}.jar').is_file():
            result['issues'].append('缺少客户端 JAR')
        missing_libs = 0
        seen_lib_paths = set()
        for _dir_id, info in chain:
            for lib in info.get('libraries') or []:
                if not isinstance(lib, dict):
                    continue
                if not self._library_applies(lib):
                    continue
                artifact = (lib.get('downloads') or {}).get('artifact') or {}
                path = artifact.get('path') or ''
                if not path:
                    if not artifact.get('url'):
                        continue
                    from core.repair import _maven_path
                    path = _maven_path(lib.get('name', ''))
                if not path:
                    continue
                if path in seen_lib_paths:
                    continue
                seen_lib_paths.add(path)
                if not (mc_root / 'libraries' / path).is_file():
                    missing_libs += 1
        if missing_libs:
            result['issues'].append(f'缺少 {missing_libs} 个依赖库')
        asset_index = data.get('assetIndex') or {}
        assets_id = data.get('assets') or (
            asset_index.get('id') if isinstance(asset_index, dict) else None
        )
        if not assets_id:
            for _dir_id, info in chain:
                if info.get('assets'):
                    assets_id = info['assets']
                    break
        if assets_id and not (mc_root / 'assets' / 'indexes' / f'{assets_id}.json').is_file():
            result['issues'].append('缺少资源索引')
        result['valid'] = not result['issues']
        return result

    def delete_version(self, version_id):
        ver_dir = self.versions_dir / version_id
        if not ver_dir.exists():
            return False
        try:
            safe_rmtree(ver_dir, ignore_errors=True)
            return True
        except Exception as e:
            logger.error(f'删除版本 {version_id} 失败: {e}')
            return False

    def rename_version(self, old_id, new_id):
        '''重命名版本目录，并同步修正 JSON id 与 JAR 文件名。

        目标名称必须不与 versions 目录中的任何文件夹/文件重名
        （Windows 下大小写不敏感），并拒绝非法与保留名称。
        '''

        old_id = str(old_id or '').strip()
        new_id = str(new_id or '').strip()
        if not old_id or not new_id or new_id in ('.', '..'):
            return False
        if re.search('[<>:"/\\\\|?*\\x00-\\x1f]', new_id):
            return False
        if new_id.endswith((' ', '.')):
            return False
        reserved = {'CON', 'PRN', 'AUX', 'NUL', *{f'COM{i}' for i in range(1, 10)}, *{f'LPT{i}' for i in range(1, 10)}}
        if new_id.split('.', 1)[0].strip().upper() in reserved:
            return False
        old_dir = self.versions_dir / old_id
        new_dir = self.versions_dir / new_id
        if not old_dir.is_dir():
            return False
        try:
            for item in self.versions_dir.iterdir():
                if item.name.casefold() == new_id.casefold():
                    return False
        except OSError:
            return False
        if new_dir.exists():
            return False
        try:
            old_dir.rename(new_dir)
            json_files = list(new_dir.glob('*.json'))
            if json_files:
                json_path = json_files[0]
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    data['id'] = new_id
                    if data.get('jar') == old_id:
                        data['jar'] = new_id
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f'修正重命名后的 JSON 失败: {e}')
                expected_json = new_dir / f'{new_id}.json'
                if json_path != expected_json:
                    if expected_json.exists():
                        expected_json.unlink()
                    json_path.rename(expected_json)
            old_jar = new_dir / f'{old_id}.jar'
            if old_jar.exists():
                old_jar.rename(new_dir / f'{new_id}.jar')
                return True
            if not (new_dir / f'{new_id}.jar').exists():
                for jar in new_dir.glob('*.jar'):
                    if jar.stem != new_id:
                        jar.rename(new_dir / f'{new_id}.jar')
                        return True
            return True
        except Exception as e:
            logger.error(f'重命名版本 {old_id} -> {new_id} 失败: {e}')
            return False

    def get_available_versions(self, version_type='release'):
        try:
            data = self.dl_helper.fetch_json('/mc/game/version_manifest.json', fallback_urls=['/version_manifest.json'])
            versions = data.get('versions', [])
            result = []
            for v in versions:
                if version_type == 'all' or v.get('type') == version_type:
                    result.append(v)
            return result
        except Exception as e:
            logger.error(f'获取版本列表失败: {e}')
            return []

    def get_fabric_supported_mc_versions(self):
        official = 'https://meta.fabricmc.net/v2/versions/game'
        bmcl_mirror = 'https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/game'
        try:
            data = self.dl_helper.fetch_json(official, fallback_urls=[bmcl_mirror])
            mc_vers = [item['version'] for item in data]
            logger.info(f'[FabricLoader] 获取到支持 Fabric 的 MC 版本，共 {len(mc_vers)} 个')
            return mc_vers
        except Exception as e:
            logger.error(f'[FabricLoader] 获取 MC 版本列表失败: {e}')
            return []

    def get_fabric_loader_list(self, mc_version: str):
        official = f'https://meta.fabricmc.net/v2/versions/loader/{mc_version}'
        bmcl_mirror = f'https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/loader/{mc_version}'
        try:
            data = self.dl_helper.fetch_json(official, fallback_urls=[bmcl_mirror])
            stable_loaders = []
            all_loaders = []
            for item in data:
                ver = item['loader']['version']
                is_stable = item['loader']['stable']
                all_loaders.append(ver)
                if is_stable:
                    stable_loaders.append(ver)
            logger.info(f'[FabricLoader] MC{mc_version} 稳定版: {stable_loaders}, 全部: {all_loaders}')
            return {'stable': stable_loaders, 'all': all_loaders, 'raw': data}
        except Exception as e:
            logger.error(f'[FabricLoader] 获取 MC{mc_version} Loader 列表失败: {e}')
            return {'stable': [], 'all': [], 'raw': []}

    def _modrinth_api_bases(self):
        source = str(self.config.get('file_download_source', 'auto') or 'auto').lower()
        official = 'https://api.modrinth.com/v2'
        mirror = 'https://mod.mcimirror.top/modrinth/v2'
        if source == 'official':
            return [official, mirror]
        return [mirror, official]

    def get_latest_fabric_api_version(self, mc_version: str) -> Optional[str]:
        params = {'game_versions': f'["{mc_version}"]', 'loaders': '["fabric"]', 'limit': 1}
        last_error = None
        for base in self._modrinth_api_bases():
            try:
                resp = requests.get(f'{base}/project/P7dR8mSH/version', params=params, timeout=15, verify=certifi.where(), headers={'User-Agent': 'PMGL-Launcher/1.0'})
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    if not data:
                        pass
                    else:
                        first = data[0]
                        if not isinstance(first, dict):
                            pass
                        else:
                            version = first.get('version_number')
                            if version:
                                logger.info(f'[FabricAPI] 获取到版本: {version} (MC {mc_version})')
                                return str(version)
            except Exception as exc:
                last_error = exc
                continue
        logger.warning(f'[FabricAPI] MC {mc_version} 无可用 API 版本: {last_error}')

    def download_fabric_api(self, version_id: str, mc_dir: str, mc_version: str, version: Optional[str]=None, progress_cb=None, cancel=(lambda: False)) -> bool:
        """下载指定 Fabric API 版本到实例对应的 mods 目录。"""

        if version is None:
            version = self.get_latest_fabric_api_version(mc_version)
            if not version:
                logger.warning('[FabricAPI] 无法获取最新版本，取消下载')
                return False
        params = {
            'game_versions': json.dumps([mc_version]),
            'loaders': json.dumps(['fabric']),
            'limit': 50,
        }
        data = None
        last_error = None
        headers = {'User-Agent': 'PMGL-Launcher/1.1', 'Accept': 'application/json'}
        for base in self._modrinth_api_bases():
            try:
                resp = requests.get(
                    f'{base}/project/P7dR8mSH/version',
                    params=params,
                    timeout=15,
                    verify=certifi.where(),
                    headers=headers,
                )
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, list):
                    continue
                for item in payload:
                    if isinstance(item, dict) and str(item.get('version_number') or '') == str(version):
                        data = item
                        break
                if data is None and payload and isinstance(payload[0], dict):
                    data = payload[0]
                if data is not None:
                    break
            except Exception as exc:
                last_error = exc
                continue
        if data is None:
            logger.error(f'[FabricAPI] 获取版本 {version} 信息失败: {last_error}')
            return False
        primary = None
        for file in data.get('files') or []:
            if isinstance(file, dict) and file.get('primary'):
                primary = file
                break
        if primary is None and data.get('files'):
            primary = data['files'][0]
        if not (isinstance(primary, dict) and primary.get('url')):
            logger.error(f'[FabricAPI] 未找到版本 {version} 的下载链接')
            return False
        download_url = str(primary['url'])
        hashes = primary.get('hashes') or {}
        from core.launch import LaunchManager
        launch_mgr = LaunchManager()
        if launch_mgr.is_version_isolated(version_id):
            mods_dir = Path(mc_dir) / 'versions' / version_id / 'mods'
        else:
            mods_dir = Path(mc_dir) / 'mods'
        mods_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(str(primary.get('filename') or f'fabric-api-{version}.jar')).name
        jar_path = mods_dir / filename
        old_jars = []
        try:
            old_jars = [
                old for old in mods_dir.glob('*.jar')
                if re.search(r'fabric[-_]api', old.name, re.I) and old != jar_path
            ]
        except OSError:
            old_jars = []
        try:
            from core.mods import download_candidates
            candidates = download_candidates(download_url)
        except Exception:
            candidates = [download_url]
        http = HttpClient(timeout=30, retries=1, verify=False)
        try:
            for url in dict.fromkeys(candidates):
                if cancel():
                    return False
                try:
                    http.download(
                        url, jar_path,
                        sha1=hashes.get('sha1') or None,
                        size=primary.get('size') or None,
                        progress=progress_cb,
                        cancel=cancel,
                        allow_slow_fallback=False,
                    )
                    logger.info(f'[FabricAPI] 下载成功: {jar_path}')
                    for old in old_jars:
                        try:
                            old.unlink(missing_ok=True)
                        except OSError:
                            pass
                    return True
                except DownloadCancelled:
                    return False
                except Exception as exc:
                    last_error = exc
                    logger.warning(f'[FabricAPI] 下载地址失败 {url}: {exc}')
                    continue
            logger.error(f'[FabricAPI] 下载失败: {last_error}')
            return False
        finally:
            try:
                http.close()
            except Exception:
                pass

    def download_optifine(self, version_id, mc_dir, optifine_version):
        '''下载 OptiFine JAR 到对应版本的 mods 目录（Forge 模式）。'''

        try:
            from core.optifine import OptiFineManager
            from core.launch import LaunchManager
            manager = OptiFineManager()
            mc_version = optifine_version.split('_')[0]
            info = manager.find(mc_version, optifine_version)
            if not info:
                logger.error(f'未找到 OptiFine 版本: {optifine_version}')
                return False
            mods_dir = LaunchManager().get_mods_dir(version_id)
            jar_path = mods_dir / info.filename
            manager.download_jar(info, jar_path)
            return jar_path.exists()
        except Exception as e:
            logger.error(f'下载 OptiFine 失败: {e}')
            return False


class ModSearchWorker(QThread):
    '''异步搜索 Modrinth 模组，支持取消、分页，避免主线程阻塞'''

    result_ready = Signal(list)
    error_occurred = Signal(str)
    progress_updated = Signal(int)
    def __init__(self, query: str='', limit: int=20, offset: int=0, mc_version: Optional[str]=None):
        super().__init__()
        self.query = query
        self.limit = limit
        self.offset = offset
        self.mc_version = mc_version
        self._cancelled = False
        self.dl_helper = DownloadHelper()

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress_updated.emit(10)
            url = 'https://api.modrinth.com/v2/search'
            params = {'query': self.query, 'limit': self.limit, 'offset': self.offset, 'facets': '[["project_type:mod"]]'}
            if self.mc_version:
                params['facets'] = f'[["project_type:mod"],["versions:{self.mc_version}"]]'
            resp = self.dl_helper.session.get(url, params=params, timeout=20, verify=certifi.where())
            if self._cancelled:
                return
            resp.raise_for_status()
            self.progress_updated.emit(60)
            data = resp.json()
            hits = data.get('hits') if isinstance(data, dict) else []
            if not isinstance(hits, list):
                hits = []
            self.progress_updated.emit(80)
            mod_list = []
            for hit in hits[:self.limit]:
                if not isinstance(hit, dict):
                    continue
                mod = {'id': hit.get('project_id', ''), 'slug': hit.get('slug', ''), 'title': hit.get('title', '未命名模组'), 'description': hit.get('description', ''), 'downloads': hit.get('downloads', 0), 'icon_url': hit.get('icon_url', ''), 'author': hit.get('author', ''), 'latest_version': hit.get('latest_version', '')}
                mod_list.append(mod)
            self.progress_updated.emit(100)
            if not self._cancelled:
                self.result_ready.emit(mod_list)
            return
        except requests.RequestException as e:
            if not self._cancelled:
                self.error_occurred.emit(f'网络错误：{str(e)}')
                return
            return
        except json.JSONDecodeError as e:
            if not self._cancelled:
                self.error_occurred.emit(f'数据解析错误：{str(e)}')
                return
            return
        except Exception as e:
            if not self._cancelled:
                self.error_occurred.emit(f'搜索失败：{str(e)}')
                return
            return


class FabricApiQueryWorker(QThread):
    '''异步获取 Fabric API 版本'''

    result_ready = Signal(str)
    error_occurred = Signal(str)
    def __init__(self, mc_version: str):
        super().__init__()
        self.mc_version = mc_version
        self._cancelled = False
        self.version_mgr = VersionManager()

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            version = self.version_mgr.get_latest_fabric_api_version(self.mc_version)
            if self._cancelled:
                return
            self.result_ready.emit(version if version is not None else '')
        except Exception as e:
            if not self._cancelled:
                self.error_occurred.emit(str(e))
                return
            return


class InstallWorker(QThread):
    status_updated = Signal(str)
    progress_updated = Signal(int)
    finished_success = Signal(str)
    finished_error = Signal(str)
    def __init__(self, version_id, mc_dir, mirror=None):
        super().__init__()
        self.version_id = version_id
        self.mc_dir = mc_dir
        self.mirror = mirror or ConfigManager().get_mirror_url() or 'https://launchermeta.mojang.com'
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            def set_status(status):
                if self._cancelled:
                    raise Exception('下载已取消')
                self.status_updated.emit(status)

            def set_progress(progress):
                if self._cancelled:
                    raise Exception('下载已取消')
                self.progress_updated.emit(progress)

            callback = {'setStatus': set_status, 'setProgress': set_progress}
            minecraft_launcher_lib.install.install_minecraft_version(self.version_id, self.mc_dir, callback=callback)
            if self._cancelled:
                return
            self.progress_updated.emit(100)
            self.status_updated.emit('下载完成')
            self.finished_success.emit(self.version_id)
        except Exception as e:
            if self._cancelled:
                self.finished_error.emit('下载已取消')
            else:
                self.finished_error.emit(str(e))
                return
            return


class FullInstallWorker(QThread):
    '''统一安装线程：原版 / Fabric / Forge / NeoForge / OptiFine / Fabric API'''

    status_updated = Signal(str)
    progress_updated = Signal(int)
    phase_updated = Signal(str, float)
    finished_success = Signal(str)
    finished_error = Signal(str)
    def __init__(self, version_id, mc_dir, custom_name, selections, mirror=None):
        super().__init__()
        self.version_id = version_id
        self.mc_dir = mc_dir
        self.custom_name = custom_name
        self.selections = selections or {}
        self.mirror = mirror or ConfigManager().get_mirror_url() or 'https://launchermeta.mojang.com'
        self._cancelled = False
        self.config = ConfigManager()
        # 版本管理器/网络会话在线程内创建，避免点击开始时在 UI 线程初始化
        self.version_mgr = None
        self._target_dir = None
        self._incomplete_marker = None
        self._target_created = False
        self._before_dirs = None
        self._install_token = f'full-{id(self)}-{int(time.time() * 1000)}'
        self._phase_lock = threading.Lock()
        self._phase_last = {}

    def cancel(self):
        self._cancelled = True

    def _check_cancel(self):
        if self._cancelled:
            raise Exception('安装已取消')

    def _status(self, text):
        self.status_updated.emit(str(text))

    def _progress(self, value):
        self.progress_updated.emit(max(0, min(100, int(value))))

    def _phase(self, key: str, percent: float):
        '''上报单个安装步骤的进度；节流并保持单调递增。'''

        try:
            value = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            return
        now = time.monotonic()
        with self._phase_lock:
            last_value, last_time = self._phase_last.get(key, (-1.0, 0.0))
            if value < last_value:
                value = last_value
            if value < 100.0 and value <= last_value and now - last_time < 0.1:
                return
            self._phase_last[key] = value, now
        self.phase_updated.emit(key, value)

    def _vanilla_phase(self, category: str, percent: float):
        key = {'libraries': 'libs', 'assets': 'assets', 'json': 'json'}.get(category)
        if key:
            self._phase(key, percent)

    def _game_phase(self, category: str, percent: float):
        key = {'libraries': 'game_libs', 'assets': 'assets', 'json': 'json'}.get(category)
        if key:
            self._phase(key, percent)

    def _should_isolate_by_default(self, version_id):
        policy = str(self.config.get('default_isolation', 'off') or 'off').lower()
        if policy == 'all':
            return True
        if policy in ('modded', 'modded_non_release'):
            return True
        if policy == 'non_release':
            try:
                base_json = Path(self.mc_dir) / 'versions' / version_id / f'{version_id}.json'
                data = json.loads(base_json.read_text(encoding='utf-8'))
                return str(data.get('type', 'release')).lower() != 'release'
            except (OSError, ValueError):
                lower = version_id.lower()
                return any((x in lower for x in ('snapshot', '-pre', '-rc', 'w')))
        return False

    def _enable_isolation(self, version_id, force=False):
        if not force and not self._should_isolate_by_default(version_id):
            mods_dir = Path(self.mc_dir) / 'mods'
            mods_dir.mkdir(parents=True, exist_ok=True)
            return mods_dir
        settings = self.config.get('version_settings', {})
        entry = settings.get(version_id, {})
        entry['isolated'] = True
        settings[version_id] = entry
        self.config.set('version_settings', settings)
        self.config.save()
        version_dir = Path(self.mc_dir) / 'versions' / version_id
        try:
            from core.interop import write_isolation_markers
            write_isolation_markers(version_dir, True)
        except Exception:
            pass
        mods_dir = version_dir / 'mods'
        mods_dir.mkdir(parents=True, exist_ok=True)
        legacy_mods = version_dir / 'game' / 'mods'
        if legacy_mods.is_dir():
            for item in legacy_mods.iterdir():
                if item.is_file():
                    dest = mods_dir / item.name
                    if not dest.exists():
                        try:
                            shutil.copy2(item, dest)
                        except OSError:
                            pass
        return mods_dir

    def _install_standalone_optifine(self, info, progress_base=20, progress_span=70):
        from core.optifine import OptiFineManager
        from core.installers import pick_java
        manager = OptiFineManager()
        temp_dir = PROJECT_TEMP / 'optifine'
        temp_dir.mkdir(exist_ok=True)
        jar_path = temp_dir / info.filename
        self._status(f'正在下载 OptiFine {info.type} {info.patch}...')
        self._phase('loader_file', 0)
        def optifine_download_progress(done, total):
            ratio = done / total if total else 1
            self._progress(progress_base + int(progress_span * 0.25 * ratio))
            self._phase('loader_file', ratio * 100)

        manager.download_jar(info, jar_path, progress=optifine_download_progress, cancel=(lambda: self._cancelled))
        self._phase('loader_file', 100)
        self._check_cancel()
        java_path = pick_java(info.mcversion)
        self._status('正在运行 OptiFine 安装器...')
        self._phase('loader_install', 5)
        generated_id = manager.install_standalone(Path(self.mc_dir), info.mcversion, jar_path, java_path, status=self._status, cancel=(lambda: self._cancelled))
        self._phase('loader_install', 100)
        generated_dir = Path(self.mc_dir) / 'versions' / generated_id
        target_dir = Path(self.mc_dir) / 'versions' / self.custom_name
        if self.custom_name != generated_id:
            if target_dir.exists():
                leftovers = [item for item in target_dir.iterdir() if item.name != INCOMPLETE_MARKER]
                if leftovers:
                    raise Exception(f'版本名称 {self.custom_name} 已存在，无法创建 OptiFine 版本。请修改顶部版本名称后重试。')
                safe_rmtree(target_dir, ignore_errors=True)
            generated_dir.rename(target_dir)
            self._fix_version_json(target_dir, self.custom_name)
            retarget_launcher_profile(self.mc_dir, generated_id, self.custom_name)
        try:
            (target_dir / INCOMPLETE_MARKER).write_text('installing\n', encoding='utf-8')
        except OSError:
            pass
        self._status('正在补全 OptiFine 依赖库...')
        install_vanilla(self.custom_name, self.mc_dir, status_cb=self._status, progress_cb=(lambda p: self._progress(90 + int(p * 0.1))), phase_cb=self._game_phase, log_cb=(lambda s: logger.info(s)), cancel=(lambda: self._cancelled))
        self._phase('game', 100)
        self._status(f'OptiFine {info.type} {info.patch} 安装完成')

    def _reset_target_for_fallback(self):
        '''OptiFine 方式 A 失败后清理本次创建的半成品版本目录。'''

        target = self._target_dir
        if target is None:
            return
        marker = self._incomplete_marker
        owned = self._target_created or marker is not None and marker.exists()
        if not owned:
            return
        try:
            if target.exists():
                safe_rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            if marker is not None:
                marker.write_text('installing\n', encoding='utf-8')
            return
        except OSError as exc:
            logger.warning(f'清理 OptiFine 备用安装目录失败: {exc}')
            return

    def _install_optifine_mod(self, opti, custom_name, opti_manager, *, progress_base=55, progress_span=20):
        '''方式 B：把 OptiFine 作为 Forge 模组放入实例 mods。'''

        self._status(f'正在下载 OptiFine {opti.type} {opti.patch}...')
        mods_dir = self._enable_isolation(custom_name, force=True)
        jar_path = Path(self.mc_dir) / 'versions' / custom_name / 'optifine_download.jar'
        opti_manager.download_jar(opti, jar_path, progress=(lambda done, total: self._progress(progress_base + int(progress_span * (done / total if total else 1)))), cancel=(lambda: self._cancelled))
        dest = mods_dir / opti.filename
        if dest.exists():
            dest.unlink()
        shutil.move(str(jar_path), str(dest))
        self._status(f'OptiFine 已放入 mods: {dest.name}')

    def _run_impl(self):
        if self.version_mgr is None:
            self.version_mgr = VersionManager(self.mc_dir)
        mc_version = self.version_id
        custom = self.custom_name.strip()
        if not custom:
            raise Exception('版本名称不能为空')
        if re.search('[<>:"/\\\\|?*\\x00-\\x1f]', custom) or custom.endswith((' ', '.')):
            raise Exception('版本名称不能包含 < > : " / \\ | ? * 等字符，也不能以空格或点结尾')
        versions_dir = Path(self.mc_dir) / 'versions'
        target_dir = versions_dir / custom
        marker = target_dir / INCOMPLETE_MARKER
        if target_dir.exists() and not marker.exists():
            raise Exception(f'版本名称 {custom} 已存在，请更换名称')
        self._target_dir = target_dir
        self._target_created = not target_dir.exists()
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            marker.write_text('installing\n', encoding='utf-8')
        except OSError:
            pass
        self._incomplete_marker = marker
        selections = {k: v for k, v in self.selections.items() if v}
        main_loader = None
        for key in ('fabric', 'forge', 'neoforge'):
            if selections.get(key):
                main_loader = key
                break
        from core.optifine import OptiFineManager
        opti_manager = OptiFineManager()
        opti = None
        if selections.get('optifine'):
            opti = opti_manager.find(mc_version, selections['optifine'])
            if not opti:
                raise Exception(f'未找到 OptiFine 版本: {selections["optifine"]}')
        if opti and main_loader == 'neoforge':
            raise Exception(f'OptiFine {opti.type} {opti.patch} 不能与 {main_loader.capitalize()} 同时安装，请先取消其中一个。')
        loader_span = 55
        if main_loader == 'fabric':
            fabric_version = str(selections.get('fabric') or '')
            if opti:
                # OptiFabric 与新版 Fabric Loader 的兼容处理：
                # 旧版 OptiFabric 需要 Loader 0.14.x（0.15 起移除了内置 tinyremapper）
                try:
                    from core.optifabric import (
                        LEGACY_FABRIC_LOADER as _LEGACY_LOADER,
                        loader_version_tuple,
                        mc_version_tuple,
                        resolve as resolve_optifabric,
                    )
                    optifile, legacy_loader = resolve_optifabric(
                        mc_version, selections.get('optifabric')
                    )
                except Exception:
                    optifile, legacy_loader = None, False
                if optifile is not None:
                    selections['optifabric'] = optifile
                if legacy_loader:
                    loader_vt = loader_version_tuple(fabric_version)
                    if loader_vt and loader_vt >= (0, 15):
                        if mc_version_tuple(mc_version) >= (1, 20, 5):
                            raise Exception(
                                '该 MC 版本需要 Fabric Loader 0.15+，但没有与其兼容的'
                                ' OptiFabric，无法同时安装 OptiFine'
                            )
                        fabric_version = _LEGACY_LOADER
                        self._status(
                            f'OptiFabric 需要旧版 Fabric Loader，已改用 {_LEGACY_LOADER}...'
                        )
            self._status(f'正在安装 Fabric {fabric_version}...')
            install_fabric(mc_version, fabric_version, self.mc_dir, custom, status_cb=self._status, progress_cb=(lambda p: self._progress(int(p * loader_span / 100))), phase_cb=self._phase, log_cb=(lambda s: logger.info(s)), cancel=(lambda: self._cancelled))
        elif main_loader == 'forge':
            self._status(f'正在安装 Forge {selections["forge"]}...')
            install_forge(mc_version, selections['forge'], self.mc_dir, custom, status_cb=self._status, progress_cb=(lambda p: self._progress(int(p * loader_span / 100))), phase_cb=self._phase, log_cb=(lambda s: logger.info(s)), cancel=(lambda: self._cancelled))
        elif main_loader == 'neoforge':
            self._status(f'正在安装 NeoForge {selections["neoforge"]}...')
            install_neoforge(mc_version, selections['neoforge'], self.mc_dir, custom, status_cb=self._status, progress_cb=(lambda p: self._progress(int(p * loader_span / 100))), phase_cb=self._phase, log_cb=(lambda s: logger.info(s)), cancel=(lambda: self._cancelled))
        else:
            self._status(f'正在安装原版 {mc_version}...')
            self._phase('game', 5)
            install_vanilla(mc_version, self.mc_dir, status_cb=self._status, progress_cb=(lambda p: self._progress(int(p * 20 / 100))), phase_cb=self._vanilla_phase, log_cb=(lambda s: logger.info(s)), cancel=(lambda: self._cancelled))
            self._phase('game_libs', 0)
            self._phase('game_libs', 100)
            self._phase('game', 100)
        self._check_cancel()
        self._touch_incomplete_marker()
        if main_loader:
            self._enable_isolation(custom, force=True)
        if opti:
            if main_loader in ('forge', 'fabric'):
                self._install_optifine_mod(opti, custom, opti_manager, progress_base=55, progress_span=20)
                if main_loader == 'fabric':
                    optifabric = selections.get('optifabric')
                    if isinstance(optifabric, dict):
                        self._status(f'正在安装 OptiFabric {optifabric.get("version", "")}...')
                        try:
                            from core.optifabric import download as download_optifabric
                            dest = download_optifabric(optifabric, Path(self.mc_dir) / 'versions' / custom, cancel=(lambda: self._cancelled))
                            self._status(f'OptiFabric 已放入 mods: {dest.name}')
                        except (InstallCancelled, DownloadCancelled):
                            raise
                        except Exception as exc:
                            raise Exception(f'OptiFabric 安装失败：{exc}') from exc
                    else:
                        self._status('未找到适配的 OptiFabric 版本，已跳过自动安装；Fabric + OptiFine 可能无法正常启动。')
            else:
                try:
                    self._status('OptiFine 安装方式 A：独立安装...')
                    self._install_standalone_optifine(opti, progress_base=20, progress_span=70)
                    self._enable_isolation(custom, force=True)
                except (InstallCancelled, DownloadCancelled):
                    raise
                except Exception as exc:
                    self._check_cancel()
                    logger.warning(f'OptiFine 独立安装失败，尝试方式 B: {exc}', exc_info=True)
                    self._status('OptiFine 方式 A 失败，正在切换到方式 B（Forge 模组）...')
                    self._reset_target_for_fallback()
                    forge_version = opti_manager.resolve_forge_version(opti)
                    if not forge_version:
                        forge_version = opti_manager.latest_forge_version(mc_version)
                    if not forge_version:
                        raise Exception(f'OptiFine 独立安装失败，且无法确定备用 Forge 版本：{exc}')
                    self._status(f'OptiFine 安装方式 B：安装 Forge {forge_version} 并作为模组加载...')
                    install_forge(mc_version, forge_version, self.mc_dir, custom, status_cb=self._status, progress_cb=(lambda p: self._progress(25 + int(p * 55 / 100))), phase_cb=self._phase, log_cb=(lambda s: logger.info(s)), cancel=(lambda: self._cancelled))
                    self._install_optifine_mod(opti, custom, opti_manager, progress_base=80, progress_span=15)
                    self._status('OptiFine 已通过方式 B（Forge 模组）安装完成')
        self._check_cancel()
        self._touch_incomplete_marker()
        if selections.get('fabric_api') and main_loader == 'fabric':
            self._status('正在下载 Fabric API...')
            self._enable_isolation(custom, force=True)
            api_ver = selections['fabric_api']
            if api_ver == 'latest':
                api_ver = None
            api_base = 75 if opti else 55
            api_span = 15 if opti else 35
            self._phase('fabric_api', 0)
            def api_progress(done, total):
                ratio = done / total if total else 1
                self._phase('fabric_api', ratio * 100)
                self._progress(api_base + int(api_span * ratio))

            success = self.version_mgr.download_fabric_api(custom, self.mc_dir, mc_version, api_ver, progress_cb=api_progress, cancel=(lambda: self._cancelled))
            if not success:
                raise Exception('Fabric API 下载失败，请检查网络后重试')
            self._phase('fabric_api', 100)
            self._status('Fabric API 下载完成')
        self._touch_incomplete_marker()
        if not main_loader and not opti:
            if custom != mc_version:
                create_alias(self.mc_dir, custom, mc_version, jar_id=mc_version)
                self._status(f'已创建版本实例 {custom}')
        self._progress(100)
        self._status('安装完成！')
        try:
            self._incomplete_marker.unlink(missing_ok=True)
        except (OSError, AttributeError):
            pass
        self.finished_success.emit(custom)

    def run(self):
        versions_dir = Path(self.mc_dir) / 'versions'
        self._before_dirs = register_active_install(self._install_token, versions_dir)
        try:
            try:
                self._run_impl()
            except Exception as e:
                self._cleanup_failed_install()
                if self._cancelled:
                    self.finished_error.emit('安装已取消')
                else:
                    logger.error(f'FullInstallWorker 异常: {e}', exc_info=True)
                    self.finished_error.emit(str(e))
        finally:
            unregister_active_install(self._install_token)

    def _touch_incomplete_marker(self):
        '''加载器安装器可能会重建版本目录，重写未完成标记以免中断版本出现在列表中。'''

        marker = self._incomplete_marker
        if marker is None:
            return
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text('installing\n', encoding='utf-8')
        except OSError:
            pass

    def _cleanup_failed_install(self):
        '''删除本次安装新产生的全部版本目录以及重试中的目标目录。'''

        versions_dir = Path(self.mc_dir) / 'versions'
        before = self._before_dirs
        if before is not None:
            try:
                for item in versions_dir.iterdir():
                    if not item.is_dir() or item.name in before:
                        continue
                    safe_rmtree(item, ignore_errors=True)
            except OSError:
                pass
        target = self._target_dir
        if target is None or not target.exists():
            return
        marker = self._incomplete_marker
        try:
            if not self._target_created:
                if marker is not None:
                    if marker.exists():
                        safe_rmtree(target, ignore_errors=True)
                        return
            return
        except OSError:
            pass

    def _fix_version_json(self, version_dir, expected_id):
        json_files = list(version_dir.glob('*.json'))
        if not json_files:
            return False
        json_path = json_files[0]
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            changed = False
            if data.get('id') != expected_id:
                data['id'] = expected_id
                changed = True
            if 'jar' in data:
                data.pop('jar', None)
                changed = True
            if changed:
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            expected_json = version_dir / f'{expected_id}.json'
            if json_path != expected_json:
                if expected_json.exists():
                    expected_json.unlink()
                json_path.rename(expected_json)
            for jar in version_dir.glob('*.jar'):
                if jar.stem != expected_id:
                    target = version_dir / f'{expected_id}.jar'
                    if not target.exists():
                        jar.rename(target)
                    return True
            return True
        except Exception as e:
            logger.error(f'修正 JSON 失败: {e}')
            return False


class VersionDeleteWorker(QThread):
    '''在后台删除版本目录，避免大版本删除时界面卡死。'''

    done = Signal(str)
    failed = Signal(str, str)

    def __init__(self, version_id, version_dir, parent=None):
        super().__init__(parent)
        self.version_id = str(version_id)
        self.version_dir = str(version_dir)

    def run(self):
        path = Path(self.version_dir)
        last_error = ''
        for _attempt in range(6):
            try:
                if path.exists():
                    safe_rmtree(path)
                self.done.emit(self.version_id)
                return
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.25)
        if not path.exists():
            self.done.emit(self.version_id)
            return
        self.failed.emit(self.version_id, last_error or '版本目录仍被占用')


def cleanup_deleted_version(config, version_id):
    '''删除版本后清理配置中的收藏、选中项、版本设置与昵称。'''

    if config is None:
        return
    changed = False
    favorites = config.get('favorited_versions', []) or []
    if version_id in favorites:
        config.set('favorited_versions', [v for v in favorites if v != version_id], auto_save=False)
        changed = True
    if config.get('selected_version') == version_id:
        config.set('selected_version', '', auto_save=False)
        changed = True
    for key in ('version_settings', 'version_nicknames'):
        values = config.get(key, {}) or {}
        if version_id in values:
            values = dict(values)
            values.pop(version_id, None)
            config.set(key, values, auto_save=False)
            changed = True
    if changed:
        config.save()


# WARNING: Decompyle incomplete
