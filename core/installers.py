'''
加载器安装引擎（Fabric / Forge / NeoForge / OptiFine）
供 FullInstallWorker 与整合包导入共用，全部优先走 BMCLAPI 镜像。
'''

from __future__ import annotations
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import (Callable, Dict, List, Optional)
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from core.config import ConfigManager
from core.paths import safe_rmtree
from core.downloading import (DownloadClient, RemoteFile, fabric_profile_urls, forge_installer_urls as build_forge_installer_urls, maven_path_urls, neoforge_installer_urls as build_neoforge_installer_urls, quilt_profile_urls)
from core.logging_setup import LOG_DIR
from core.net import (DownloadCancelled, DownloadError, DownloadGate, HttpClient)
from core.paths import scl_dir
from core.repair import RepairEngine, _rewrite_mojang_url
logger = logging.getLogger('Installers')
PROJECT_TEMP = scl_dir() / 'temp_install'
PROJECT_TEMP.mkdir(parents=True, exist_ok=True)
BMCLAPI = 'https://bmclapi2.bangbang93.com'
OPENBMCLAPI = 'https://openbmclapi.bangbang93.com'

class InstallCancelled(Exception):
    pass

class ProgressMapper:
    def __init__(self, base, span, callback=None):
        self.base = base
        self.span = span
        self.callback = callback or (lambda _p: None)

    def __call__(self, percent):
        value = int(self.base + max(0, min(100, percent)) * self.span / 100)
        self.callback(max(0, min(100, value)))


def make_lib_callback(status_cb, progress_mapper):
    state = {'max': 100}
    def set_status(text):
        status_cb(str(text))

    def set_max(value):
        try:
            state['max'] = max(1, int(value))
        except Exception:
            state['max'] = 100

    def set_progress(value):
        try:
            progress_mapper(max(0, min(100, int(value) * 100 / state['max'])))
        except Exception:
            pass

    return {'setStatus': set_status, 'setMax': set_max, 'setProgress': set_progress}

def ensure_launcher_profiles(mc_dir):
    path = Path(mc_dir) / 'launcher_profiles.json'
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault('profiles', {})
            return
        except Exception:
            pass
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({'profiles': {}}, fh, ensure_ascii=False, indent=2)

def _natural_key(text):
    return [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in re.split('(\\d+)', text)]

def pick_java(mc_version):
    '''选择最接近需求版本的 Java，避免用 Java 25 跑 1.12 的 Forge 安装器。'''

    try:
        from core.launch import LaunchManager
        mgr = LaunchManager()
        required = mgr._get_required_java_version(mc_version)
        _ver, path = mgr._select_best_java(required)
        if path:
            if path.lower().endswith('javaw.exe'):
                java_exe = path[:-len('javaw.exe')] + 'java.exe'
                if os.path.isfile(java_exe):
                    path = java_exe
            return path
        return mgr.detect_java() or 'java'
    except Exception:
        return 'java'

def _patch_installer_offline(installer):
    '''移除会强制访问 Mojang 的 DOWNLOAD_MOJMAPS 处理器，生成离线安装器副本。'''

    installer = Path(installer)
    try:
        with zipfile.ZipFile(installer) as zf:
            if 'install_profile.json' not in zf.namelist():
                return installer
            profile = json.loads(zf.read('install_profile.json').decode('utf-8'))
        processors = profile.get('processors') or []
        def uses_mojmaps(step):
            if not isinstance(step, dict):
                return False
            for value in step.get('args') or []:
                if isinstance(value, str) and 'DOWNLOAD_MOJMAPS' in value:
                    return True
            return False
        filtered = [step for step in processors if not uses_mojmaps(step)]
        if len(filtered) == len(processors):
            return installer
        profile['processors'] = filtered
        patched = installer.with_name(installer.stem + '-offline.jar')
        with zipfile.ZipFile(installer) as src, zipfile.ZipFile(patched, 'w', zipfile.ZIP_DEFLATED) as out:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == 'install_profile.json':
                    data = json.dumps(profile, ensure_ascii=False).encode('utf-8')
                out.writestr(item, data)
        return patched
    except Exception:
        return installer

def _run_installer(java_path, installer, args, mc_dir, *, status_cb, log_path, maven_mirror=True, timeout=900, jvm_args=None, cancel=(lambda: False)):
    ensure_launcher_profiles(mc_dir)
    cmd = [java_path]
    if jvm_args:
        cmd.extend((str(arg) for arg in jvm_args))
    if maven_mirror:
        cmd.append(f'-Dmaven.repo.remote={BMCLAPI}/maven')
    cmd += ['-jar', str(installer)] + args
    log_path.parent.mkdir(parents=True, exist_ok=True)
    status_cb('正在运行安装器（下载依赖可能需要几分钟）...')
    with open(log_path, 'w', encoding='utf-8', errors='replace') as log_fh:
        proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, cwd=str(log_path.parent), creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        start = time.time()
        while proc.poll() is None:
            if cancel():
                try:
                    proc.kill()
                except Exception:
                    raise InstallCancelled()
            if time.time() - start > timeout:
                try:
                    proc.kill()
                except Exception:
                    raise DownloadError('安装器运行超时')
            time.sleep(0.3)
        return proc.returncode
    return proc.returncode

def _tail_log(path, limit=4000):
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
        return text[-limit:]
    except Exception:
        return ''

def _is_zip_archive(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return bool(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return False

def _download_installer(candidates, dest, *, http=None, status_cb=(lambda _s: None), progress_cb=None, cancel=(lambda: False)):
    '''依次尝试镜像/官方源，避免单一下载地址失效导致安装失败。'''

    http = http or HttpClient(timeout=60)
    last_error = None
    for index, url in enumerate(candidates):
        if cancel():
            raise InstallCancelled()
        if index:
            status_cb('下载源不可用，正在切换备用源...')
        try:
            if dest.exists():
                dest.unlink(missing_ok=True)
            http.download(url, dest, cancel=cancel, allow_slow_fallback=False, progress=(lambda done, total: progress_cb(int(done * 100 / total)) if total else 0) if progress_cb else None)
            if _is_zip_archive(dest):
                return dest
            last_error = DownloadError('安装器文件无效')
        except (InstallCancelled, DownloadCancelled):
            raise
        except Exception as exc:
            last_error = exc
        if dest.exists():
            dest.unlink(missing_ok=True)
    raise DownloadError(f'安装器下载失败：{last_error}')

def retarget_launcher_profile(mc_dir, old_id, new_id):
    '''安装器生成的版本目录被改名后，同步修正 launcher_profiles.json。'''

    path = Path(mc_dir) / 'launcher_profiles.json'
    if not path.is_file() or not old_id or old_id == new_id:
        return
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    profiles = data.get('profiles')
    if not isinstance(profiles, dict):
        return
    changed = False
    for profile in profiles.values():
        if isinstance(profile, dict) and profile.get('lastVersionId') == old_id:
            profile['lastVersionId'] = new_id
            changed = True
    if not changed:
        return
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass

def _rename_generated_version(versions_dir, generated, custom_name):
    '''把安装器生成的版本目录改名为用户指定的实例名，避免重复版本。'''

    generated = Path(generated)
    if generated.name == custom_name:
        return generated
    dest = versions_dir / custom_name
    if dest.exists():
        raise DownloadError(f'版本名称 {custom_name} 已存在，无法完成安装')
    generated.rename(dest)
    old_id = generated.name
    json_path = dest / f'{old_id}.json'
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding='utf-8'))
            data['id'] = custom_name
            new_json = dest / f'{custom_name}.json'
            new_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            if new_json != json_path:
                json_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    old_jar = dest / f'{old_id}.jar'
    if old_jar.exists():
        try:
            old_jar.rename(dest / f'{custom_name}.jar')
        except OSError:
            pass
    retarget_launcher_profile(str(versions_dir.parent), old_id, custom_name)
    return dest

def _prepare_reinstall_target(target_dir, custom_name):
    '''方案 A 失败后清掉半成品 JSON，允许方案 B 复用同一目标目录。'''

    target_dir = Path(target_dir)
    if not target_dir.exists():
        return
    for item in target_dir.iterdir():
        if item.name == f'{custom_name}.json':
            try:
                item.unlink()
            except OSError:
                pass

def _remove_precreated_target(target_dir):
    '''删除安装开始时预创建的空实例目录（带 .pmgl_incomplete 标记）。'''

    target_dir = Path(target_dir)
    if not target_dir.exists():
        return False
    if not (target_dir / '.pmgl_incomplete').exists():
        raise DownloadError(f'版本名称 {target_dir.name} 已存在，无法完成安装')
    safe_rmtree(target_dir, ignore_errors=True)
    return True

def install_vanilla(mc_version, mc_dir, *, status_cb=(lambda _s: None), progress_cb=None, phase_cb=None, log_cb=(lambda _s: None), cancel=(lambda: False), fast=True):
    engine = RepairEngine(mc_version, mc_dir, status=status_cb, progress=progress_cb or (lambda _p: None), phase=phase_cb, log=log_cb, cancel=cancel, fast=fast)
    result = engine.repair()
    if result.failed:
        raise DownloadError('原版文件下载不完整：' + '、'.join(result.failed_items[:5]))

def find_version_dir(mc_dir, mc_version, keyword, before=None):
    versions_dir = Path(mc_dir) / 'versions'
    if not versions_dir.exists():
        return
    if before is not None:
        new_dirs = [item for item in versions_dir.iterdir() if item.is_dir() if item.name not in before]
        if len(new_dirs) == 1:
            return new_dirs[0]
    candidates = []
    for item in versions_dir.iterdir():
        if not item.is_dir():
            continue
        name = item.name.lower()
        if keyword not in name:
            continue
        if mc_version and not name.startswith(mc_version.lower()) and keyword != 'neoforge':
            continue
        if before is not None and item.name in before:
            continue
        candidates.append(item)
    if not candidates:
        for item in versions_dir.iterdir():
            if item.is_dir() and keyword in item.name.lower():
                candidates.append(item)
    if not candidates:
        return
    candidates.sort(key=(lambda p: p.stat().st_mtime), reverse=True)
    return candidates[0]

def _maven_path_from_coord(coord):
    '''把 group:artifact:version[:classifier][@ext] 转成 maven 相对路径。'''

    if not coord:
        return ''
    coord = str(coord).strip()
    if coord.startswith('[') and coord.endswith(']'):
        coord = coord[1:-1]
    name, _, ext = coord.partition('@')
    parts = name.split(':')
    if len(parts) < 3:
        return ''
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ''
    filename = f'{artifact}-{version}'
    if classifier:
        filename += f'-{classifier}'
    filename += f'.{ext or "jar"}'
    return '/'.join(group.split('.')) + f'/{artifact}/{version}/{filename}'

def _prefetch_mojmaps(profile, mc_dir, *, status_cb=(lambda _s: None), cancel=(lambda: False)):
    '''预放置 Mojang 映射表，避免 Forge 安装器在处理器阶段访问 Mojang。'''

    if not profile:
        return False
    mc_dir_path = Path(mc_dir)
    mc_version = str(profile.get('minecraft') or '')
    mcp_version = ''
    data = profile.get('data') or {}
    if isinstance(data.get('MCP_VERSION'), dict):
        mcp_version = str(data['MCP_VERSION'].get('client') or '').strip('\'"')
    if not mc_version or not mcp_version:
        return False
    version_json = mc_dir_path / 'versions' / mc_version / f'{mc_version}.json'
    version_data = None
    if version_json.exists():
        try:
            with open(version_json, 'r', encoding='utf-8') as fh:
                version_data = json.load(fh)
        except Exception:
            version_data = None
    if not isinstance(version_data, dict):
        try:
            from core.downloading import fetch_version_json
            version_data = fetch_version_json(mc_version, source='bmclapi', timeout=20)
        except Exception:
            return False
    downloads = version_data.get('downloads') or {}
    http = HttpClient(timeout=180, retries=4)
    ok = True
    mapping_version = mcp_version if mcp_version.startswith(f'{mc_version}-') else f'{mc_version}-{mcp_version}'
    for side, key in (('client', 'client_mappings'), ('server', 'server_mappings')):
        info = downloads.get(key) or {}
        url = info.get('url')
        if not url:
            continue
        dest = mc_dir_path / 'libraries' / 'net' / 'minecraft' / side / mapping_version / f'{side}-{mapping_version}-mappings.txt'
        if dest.exists() and dest.stat().st_size > 0:
            continue
        if cancel():
            raise InstallCancelled()
        status_cb(f'正在准备 Mojang 映射表 ({side})...')
        saved = False
        source = str(ConfigManager().get('file_download_source', 'auto') or 'auto')
        if source == 'official':
            candidates = [url]
        elif source == 'bmclapi':
            candidates = [_rewrite_mojang_url(url), _rewrite_mojang_url(url, OPENBMCLAPI), url]
        else:
            candidates = [url, _rewrite_mojang_url(url), _rewrite_mojang_url(url, OPENBMCLAPI)]
        for candidate in dict.fromkeys(candidates):
            try:
                http.download(candidate, dest, sha1=info.get('sha1'), size=info.get('size'), cancel=cancel)
                saved = True
                break
            except InstallCancelled:
                raise
            except Exception:
                pass
        if not saved:
            ok = False
    return ok

def prefetch_installer_libraries(installer, mc_dir, *, status_cb=(lambda _s: None), progress_cb=None, cancel=(lambda: False)):
    '''预下载 Forge/NeoForge 安装器需要的所有 maven 依赖。

    安装器自身只会访问 maven.minecraftforge.net / maven.neoforged.net，
    在国内网络下经常失败。这里先通过 BMCLAPI 的 /maven 镜像补齐，
    安装器运行时就会直接跳过下载。
    '''

    mc_dir_path = Path(mc_dir)
    libs_dir = mc_dir_path / 'libraries'
    profile = None
    version = None
    with zipfile.ZipFile(installer) as zf:
        names = set(zf.namelist())
        if 'install_profile.json' in names:
            profile = json.loads(zf.read('install_profile.json').decode('utf-8'))
        if 'version.json' in names:
            version = json.loads(zf.read('version.json').decode('utf-8'))
        tasks = {}
        def is_generated(path):
            lowered = path.lower()
            if '/net/minecraft/client/' in f'/{lowered}' or '/net/minecraft/server/' in f'/{lowered}':
                name = Path(path).name.lower()
                if name.endswith('-mappings.txt'):
                    return False
                return True
            if ('/net/minecraftforge/forge/' in f'/{lowered}' or '/net/neoforged/forge/' in f'/{lowered}' or '/net/neoforged/neoforge/' in f'/{lowered}') and (lowered.endswith('-client.jar') or lowered.endswith('-server.jar')):
                return True
            if lowered.endswith('-mappings-merged.txt'):
                return True
            return False

        def add_task(path, url='', sha1='', size=None):
            if not path or path in tasks or is_generated(path):
                return
            tasks[path] = {'url': url, 'sha1': sha1 or '', 'size': size}

        for data in profile or {}, version or {}, (profile or {}).get('versionInfo') or {}:
            for lib in data.get('libraries', []) or []:
                if not isinstance(lib, dict):
                    continue
                art = (lib.get('downloads') or {}).get('artifact') or {}
                path = art.get('path') or _maven_path_from_coord(lib.get('name', ''))
                if path:
                    add_task(path, art.get('url', ''), art.get('sha1', ''), art.get('size'))
        if profile:
            for processor in profile.get('processors', []) or []:
                coords = [processor.get('jar')] + list(processor.get('classpath', []) or [])
                for coord in coords:
                    path = _maven_path_from_coord(coord)
                    if path:
                        add_task(path)
            for entry in (profile.get('data') or {}).values():
                if not isinstance(entry, dict):
                    continue
                for value in entry.values():
                    if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
                        path = _maven_path_from_coord(value)
                        if path:
                            add_task(path)
        total = max(1, len(tasks))
        completed = 0
        progress_lock = threading.Lock()
        zip_lock = threading.Lock()
        client = DownloadClient(mc_dir, fast=False)
        def fetch_library(path, meta):
            if cancel():
                raise InstallCancelled()
            dest = libs_dir / Path(*path.split('/'))
            if dest.exists() and dest.stat().st_size > 0 and not meta['sha1']:
                return
            status_cb(f'预下载安装器依赖 {dest.name}')
            remote = RemoteFile(
                label=f'安装器依赖 {dest.name}',
                urls=maven_path_urls(path, meta['url']),
                dest=dest, sha1=meta['sha1'] or '', size=meta['size'],
                check_zip=dest.suffix.lower() in ('.jar', '.zip'),
            )
            if remote.valid('full'):
                return
            try:
                client.download(remote, cancel=cancel)
                return
            except (DownloadCancelled, InstallCancelled):
                raise
            except Exception:
                pass
            member = f'maven/{path}'
            data = None
            if member in names:
                try:
                    with zip_lock:
                        data = zf.read(member)
                except (OSError, KeyError):
                    data = None
            if data:
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                except OSError:
                    pass

        worker_count = max(1, min(DownloadGate.max_threads(), 6, len(tasks)))
        cancelled = False
        try:
            if tasks:
                with ThreadPoolExecutor(max_workers=worker_count) as pool:
                    futures = {pool.submit(fetch_library, path, meta): path for path, meta in tasks.items()}
                    for future in as_completed(futures):
                        path = futures[future]
                        try:
                            future.result()
                        except InstallCancelled:
                            cancelled = True
                        except Exception:
                            pass
                        with progress_lock:
                            completed += 1
                            done = completed
                        if progress_cb:
                            progress_cb(int(done * 100 / total))
                        if cancelled:
                            for pending in futures:
                                pending.cancel()
                            break
        finally:
            client.close()
        if cancelled or cancel():
            raise InstallCancelled()
        _prefetch_mojmaps(profile, mc_dir, status_cb=status_cb, cancel=cancel)

def _install_patched_client(profile, mc_dir, generated_dir, generated_id):
    '''把安装器生成的 patched client jar 复制为版本主 JAR。'''

    target = generated_dir / f'{generated_id}.jar'
    if not profile:
        return target.exists()
    data = profile.get('data') or {}
    patched = data.get('PATCHED') if isinstance(data, dict) else None
    coord = ''
    if isinstance(patched, dict):
        coord = str(patched.get('client') or '')
    path = _maven_path_from_coord(coord)
    if not path:
        return target.exists()
    src = Path(mc_dir) / 'libraries' / Path(*path.split('/'))
    if not src.exists():
        return target.exists()
    if target.exists() and target.stat().st_size == src.stat().st_size:
        return True
    try:
        shutil.copy2(src, target)
        return True
    except OSError:
        return False

def create_alias(mc_dir, alias_id, parent_id, jar_id=None):
    '''创建继承父版本的轻量版本 JSON，用于整合包/独立实例。'''

    mc_dir = Path(mc_dir)
    versions_dir = mc_dir / 'versions'
    parent_json = versions_dir / parent_id / f'{parent_id}.json'
    if not parent_json.exists():
        raise DownloadError(f'父版本 {parent_id} 未安装，无法创建实例 {alias_id}')
    alias_dir = versions_dir / alias_id
    alias_dir.mkdir(parents=True, exist_ok=True)
    data = {'id': alias_id, 'inheritsFrom': parent_id, 'type': 'release'}
    if jar_id:
        data['jar'] = jar_id
    with open(alias_dir / f'{alias_id}.json', 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return alias_dir

def _fabric_installer_version(http):
    for url in f'{BMCLAPI}/fabric-meta/v2/versions/installer', 'https://meta.fabricmc.net/v2/versions/installer', f'{OPENBMCLAPI}/fabric-meta/v2/versions/installer':
        try:
            data = http.get_json(url)
        except Exception:
            pass
        else:
            if not isinstance(data, list):
                continue
            for row in data:
                if isinstance(row, dict) and row.get('stable') and row.get('version'):
                    return str(row['version'])
            for row in data:
                if isinstance(row, dict) and row.get('version'):
                    return str(row['version'])
    return ''

def _install_fabric_with_installer(mc_version, loader_version, mc_dir, custom_name, *, status_cb=(lambda _s: None), progress_cb=None, phase_cb=None, log_cb=(lambda _s: None), cancel=(lambda: False)):
    '''安装方式 B：运行 Fabric 官方安装器，A 方案不可用时使用。'''

    emit = phase_cb or (lambda _k, _p: None)
    mc_dir_path = Path(mc_dir)
    versions_dir = mc_dir_path / 'versions'
    temp_dir = PROJECT_TEMP / 'fabric_installer'
    temp_dir.mkdir(parents=True, exist_ok=True)
    http = HttpClient(timeout=60)
    status_cb('安装方式 B：准备 Fabric 官方安装器...')
    emit('loader_file', 0)
    inst_version = _fabric_installer_version(http) or '1.1.2'
    installer = temp_dir / f'fabric-installer-{inst_version}.jar'
    _download_installer([f'https://maven.fabricmc.net/net/fabricmc/fabric-installer/{inst_version}/fabric-installer-{inst_version}.jar', f'{BMCLAPI}/maven/net/fabricmc/fabric-installer/{inst_version}/fabric-installer-{inst_version}.jar', f'{OPENBMCLAPI}/maven/net/fabricmc/fabric-installer/{inst_version}/fabric-installer-{inst_version}.jar'], installer, http=http, status_cb=status_cb, progress_cb=(lambda p: emit('loader_file', p * 0.9)), cancel=cancel)
    emit('loader_file', 100)
    ProgressMapper(25, 10, progress_cb)(100)
    java_path = pick_java(mc_version)
    log_path = LOG_DIR / f'install_fabric_{mc_version}_{loader_version}_installer.log'
    before = {p.name for p in versions_dir.iterdir() if p.is_dir()} if versions_dir.exists() else set()
    _prepare_reinstall_target(versions_dir / custom_name, custom_name)
    installer_args = ['client', '-dir', str(mc_dir_path), '-mcversion', mc_version, '-loader', loader_version]
    jvm_args = ['-Dsun.net.client.defaultConnectTimeout=30000', '-Dsun.net.client.defaultReadTimeout=60000']
    emit('loader_install', 5)
    rc = _run_installer(java_path, installer, installer_args, mc_dir_path, status_cb=status_cb, log_path=log_path, maven_mirror=False, jvm_args=jvm_args, cancel=cancel)
    if rc != 0:
        status_cb('Fabric 安装器未完成，正在重试一次...')
        rc = _run_installer(java_path, installer, installer_args, mc_dir_path, status_cb=status_cb, log_path=log_path, maven_mirror=False, jvm_args=jvm_args, cancel=cancel)
    if rc != 0:
        raise DownloadError(f'Fabric 安装器返回错误码 {rc}：\n{_tail_log(log_path)}')
    emit('loader_install', 100)
    ProgressMapper(35, 20, progress_cb)(100)
    generated = None
    if versions_dir.exists():
        new_dirs = [item for item in versions_dir.iterdir() if item.is_dir() if item.name not in before]
        generated = next((d for d in new_dirs if 'fabric-loader' in d.name.lower()), None)
    if generated is None:
        expected = f'fabric-loader-{loader_version}-{mc_version}'
        if (versions_dir / expected).is_dir():
            generated = versions_dir / expected
    if generated is None:
        raise DownloadError('Fabric 安装器未生成版本目录，请查看日志：' + str(log_path))
    _remove_precreated_target(versions_dir / custom_name)
    target_dir = _rename_generated_version(versions_dir, generated, custom_name)
    try:
        (target_dir / '.pmgl_incomplete').write_text('installing\n', encoding='utf-8')
    except OSError:
        pass
    log_cb(f'Fabric 安装器已生成版本: {target_dir.name}')
    status_cb('正在补全 Fabric 依赖库...')
    emit('game', 5)
    def final_phase(category, percent):
        key = {'libraries': 'game_libs', 'assets': 'assets', 'json': 'json'}.get(category)
        if key:
            emit(key, percent)

    engine = RepairEngine(custom_name, mc_dir, status=status_cb, progress=ProgressMapper(55, 45, progress_cb), phase=final_phase, log=log_cb, cancel=cancel, fast=True)
    result = engine.repair()
    if result.failed:
        raise DownloadError('Fabric 依赖下载不完整：' + '、'.join(result.failed_items[:5]))
    emit('game', 100)
    try:
        (target_dir / '.pmgl_incomplete').unlink(missing_ok=True)
        return custom_name
    except OSError:
        return custom_name

def install_fabric(mc_version, loader_version, mc_dir, custom_name, *, method='auto', status_cb=(lambda _s: None), progress_cb=None, phase_cb=None, log_cb=(lambda _s: None), cancel=(lambda: False)):
    '''安装 Fabric：方式 A 直接合并 meta profile，失败时自动切换方式 B。

    原版文件与 Fabric 配置/依赖同时准备，各下载步骤独立上报进度。
    '''

    mode = (method or 'auto').strip().upper()
    emit = phase_cb or (lambda _k, _p: None)
    def vanilla_phase(category, percent):
        key = {'libraries': 'libs', 'assets': 'assets', 'json': 'json'}.get(category)
        if key:
            emit(key, percent)

    def prepare_vanilla():
        status_cb(f'正在准备原版 {mc_version}...')
        install_vanilla(mc_version, mc_dir, status_cb=status_cb, progress_cb=ProgressMapper(0, 25, progress_cb), phase_cb=vanilla_phase, log_cb=log_cb, cancel=cancel)

    first_error = None
    profile_ready = False
    if mode in ('AUTO', 'A'):
        errors = []
        def prepare_profile():
            status_cb('安装方式 A：直接合并 Fabric 配置...')
            emit('loader_file', 0)
            http = HttpClient(timeout=20)
            profile = None
            errors_text = []
            for url in fabric_profile_urls(mc_version, loader_version):
                try:
                    data = http.get_json(url)
                    if isinstance(data, dict):
                        profile = data
                        break
                except Exception as exc:
                    errors_text.append(str(exc))
            if profile is None:
                if errors_text:
                    raise DownloadError(f'无法获取 Fabric 配置：{errors_text[-1]}')
                raise DownloadError('无法获取 Fabric 配置：未知错误')
            emit('loader_file', 80)
            if cancel():
                raise InstallCancelled()
            versions_dir = Path(mc_dir) / 'versions'
            target_dir = versions_dir / custom_name
            target_dir.mkdir(parents=True, exist_ok=True)
            profile['id'] = custom_name
            if 'inheritsFrom' not in profile:
                profile['inheritsFrom'] = mc_version
            profile.setdefault('jar', mc_version)
            with open(target_dir / f'{custom_name}.json', 'w', encoding='utf-8') as fh:
                json.dump(profile, fh, ensure_ascii=False, indent=2)
            log_cb(f'已生成 Fabric 版本 JSON: {custom_name}')
            emit('loader_file', 100)
            emit('loader_install', 100)

        with ThreadPoolExecutor(max_workers=2) as pool:
            vanilla_future = pool.submit(prepare_vanilla)
            profile_future = pool.submit(prepare_profile)
            try:
                vanilla_future.result()
            except (InstallCancelled, DownloadCancelled):
                raise
            except Exception as exc:
                raise DownloadError(f'Fabric {loader_version} 运行环境准备失败：{exc}') from exc
            try:
                profile_future.result()
            except (InstallCancelled, DownloadCancelled):
                raise
            except Exception as exc:
                errors.append(exc)
        if errors:
            first_error = errors[0]
            if mode == 'A':
                raise first_error
            status_cb(f'安装方式 A 失败（{first_error}），正在切换到安装方式 B...')
            log_cb(f'Fabric 方式 A 失败: {first_error}')
        else:
            profile_ready = True
    if profile_ready:
        try:
            status_cb('正在补全 Fabric 依赖库...')
            emit('game', 5)
            def final_phase(category, percent):
                key = {'libraries': 'game_libs', 'assets': 'assets', 'json': 'json'}.get(category)
                if key:
                    emit(key, percent)

            engine = RepairEngine(custom_name, mc_dir, status=status_cb, progress=ProgressMapper(25, 75, progress_cb), phase=final_phase, log=log_cb, cancel=cancel, fast=True)
            result = engine.repair()
            if result.failed:
                raise DownloadError('Fabric 依赖下载不完整：' + '、'.join(result.failed_items[:5]))
            emit('game', 100)
            return custom_name
        except (InstallCancelled, DownloadCancelled):
            raise
        except Exception as exc:
            first_error = exc
            if mode == 'A':
                raise
            status_cb(f'安装方式 A 失败（{exc}），正在切换到安装方式 B...')
            log_cb(f'Fabric 方式 A 失败: {exc}')
    if mode == 'B':
        prepare_vanilla()
    try:
        return _install_fabric_with_installer(mc_version, loader_version, mc_dir, custom_name, status_cb=status_cb, progress_cb=progress_cb, phase_cb=emit, log_cb=log_cb, cancel=cancel)
    except (InstallCancelled, DownloadCancelled):
        raise
    except Exception as exc:
        if first_error is not None:
            raise DownloadError(f'Fabric 安装失败。\n方式 A：{first_error}\n方式 B：{exc}') from exc
        raise

def install_quilt(mc_version, loader_version, mc_dir, custom_name, *, method='auto', status_cb=(lambda _s: None), progress_cb=None, phase_cb=None, log_cb=(lambda _s: None), cancel=(lambda: False)):
    '''安装 Quilt：读取官方/镜像 profile JSON 后生成版本并补全依赖。'''

    emit = phase_cb or (lambda _k, _p: None)
    status_cb(f'正在准备原版 {mc_version}...')
    def vanilla_phase(category, percent):
        key = {'libraries': 'libs', 'assets': 'assets', 'json': 'json'}.get(category)
        if key:
            emit(key, percent)
    install_vanilla(mc_version, mc_dir, status_cb=status_cb, progress_cb=ProgressMapper(0, 25, progress_cb), phase_cb=vanilla_phase, log_cb=log_cb, cancel=cancel)
    if cancel():
        raise InstallCancelled()
    status_cb('正在获取 Quilt 配置...')
    http = HttpClient(timeout=20)
    profile = None
    errors = []
    for url in quilt_profile_urls(mc_version, loader_version):
        try:
            data = http.get_json(url)
        except Exception as exc:
            errors.append(str(exc))
            continue
        if isinstance(data, dict) and data.get('libraries'):
            profile = data
            break
    if profile is None:
        raise DownloadError(f'无法获取 Quilt {loader_version} 配置：{errors[-1] if errors else "网络不可用"}')
    target_dir = Path(mc_dir) / 'versions' / custom_name
    target_dir.mkdir(parents=True, exist_ok=True)
    profile['id'] = custom_name
    profile.setdefault('inheritsFrom', mc_version)
    profile.setdefault('jar', mc_version)
    with open(target_dir / f'{custom_name}.json', 'w', encoding='utf-8') as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=2)
    _mark_mc_version(target_dir, mc_version)
    emit('loader_file', 100)
    emit('loader_install', 100)
    status_cb('正在补全 Quilt 依赖库...')
    engine = RepairEngine(custom_name, mc_dir, status=status_cb, progress=ProgressMapper(25, 75, progress_cb), phase=emit, log=log_cb, cancel=cancel, fast=True)
    result = engine.repair()
    if result.failed:
        raise DownloadError('Quilt 依赖下载不完整：' + '、'.join(result.failed_items[:5]))
    return custom_name

def _profile_is_legacy_forge(profile):
    return isinstance(profile, dict) and isinstance(profile.get('versionInfo'), dict)

def _install_legacy_forge(installer, profile, mc_dir, mc_version, forge_version, *, status_cb=(lambda _s: None), cancel=(lambda: False)):
    '''旧版 Forge 安装器没有 --installClient，直接按 versionInfo 完成客户端安装。'''

    install = profile.get('install') or {}
    version_info = profile.get('versionInfo') or {}
    target = str(install.get('target') or version_info.get('id') or '').strip()
    if not target:
        raise DownloadError('旧版 Forge 安装器缺少目标版本信息')
    if cancel():
        raise InstallCancelled()
    vi = json.loads(json.dumps(version_info))
    vi['id'] = target
    vi.setdefault('type', 'release')
    if mc_version:
        vi['inheritsFrom'] = mc_version
    if not vi.get('jar'):
        vi['jar'] = mc_version
    versions_dir = mc_dir / 'versions'
    target_dir = versions_dir / target
    target_dir.mkdir(parents=True, exist_ok=True)
    target_json = target_dir / f'{target}.json'
    target_json.write_text(json.dumps(vi, ensure_ascii=False, indent=2), encoding='utf-8')
    coord = str(install.get('path') or '').strip()
    if not coord:
        for lib in vi.get('libraries') or []:
            name = str((lib or {}).get('name') or '')
            if name.startswith('net.minecraftforge:forge:'):
                coord = name
                break
    maven_path = _maven_path_from_coord(coord)
    if not maven_path:
        raise DownloadError('旧版 Forge 安装器缺少 universal jar 坐标')
    file_path = str(install.get('filePath') or '').strip()
    dest = mc_dir / 'libraries' / Path(*maven_path.split('/'))
    dest.parent.mkdir(parents=True, exist_ok=True)
    copied = False
    with zipfile.ZipFile(installer) as zf:
        names = set(zf.namelist())
        member = file_path if file_path in names else ''
        if not member:
            candidates = [name for name in names if name.lower().endswith('-universal.jar')]
            if candidates:
                member = candidates[0]
        if member:
            with zf.open(member) as src:
                with open(dest, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
            copied = True
    if not copied:
        raise DownloadError('旧版 Forge 安装器内未找到 universal jar')
    log_path = LOG_DIR / f'install_{mc_version}-{forge_version}_legacy.log'
    log_path.write_text(f'Legacy Forge installer\ntarget={target}\nminecraft={mc_version}\nuniversal={dest}\n', encoding='utf-8')
    status_cb(f'已通过旧版兼容流程安装 Forge {target}')
    return target_dir

def _forge_installer_urls(mc_version, forge_version, official_first=False):
    return build_forge_installer_urls(mc_version, forge_version, prefer_official=official_first)

def _read_forge_profile(installer):
    try:
        with zipfile.ZipFile(installer) as zf:
            if 'install_profile.json' in zf.namelist():
                return json.loads(
                    zf.read('install_profile.json').decode('utf-8')
                )
    except Exception:
        pass
    return {}

def install_forge(mc_version, forge_version, mc_dir, custom_name, *, method='auto', status_cb=(lambda _s: None), progress_cb=None, phase_cb=None, log_cb=(lambda _s: None), cancel=(lambda: False)):
    '''安装 Forge。

    方式 A：预下载依赖后离线安装（现代安装器）/ 兼容流程（旧版安装器）。
    方式 B：镜像在线安装；旧版安装器则改用官方安装器重试。
    auto 模式默认先走 A，失败自动切换 B。
    '''

    mode = (method or 'auto').strip().upper()
    emit = phase_cb or (lambda _k, _p: None)
    mc_dir_path = Path(mc_dir)
    versions_dir = mc_dir_path / 'versions'
    before = {p.name for p in versions_dir.iterdir() if p.is_dir()} if versions_dir.exists() else set()
    status_cb(f'正在准备 Forge {forge_version} 运行环境...')
    temp_dir = PROJECT_TEMP / 'forge_installer'
    temp_dir.mkdir(exist_ok=True)
    installer = temp_dir / f'forge-installer-{forge_version}.jar'
    progress_state = {'vanilla': 0, 'prepare': 0}
    progress_lock = threading.Lock()
    def vanilla_phase(category, percent):
        key = {'libraries': 'libs', 'assets': 'assets', 'json': 'json'}.get(category)
        if key:
            emit(key, percent)

    def update_progress(key, value):
        with progress_lock:
            progress_state[key] = max(progress_state[key], max(0, min(100, value)))
            combined = int(progress_state['vanilla'] * 0.25 + progress_state['prepare'] * 0.25)
        if progress_cb:
            progress_cb(min(50, combined))

    def install_runtime():
        install_vanilla(mc_version, mc_dir, status_cb=status_cb, progress_cb=(lambda p: update_progress('vanilla', p)), phase_cb=vanilla_phase, log_cb=log_cb, cancel=cancel)

    prepare_result = {}
    def prepare_installer():
        http = HttpClient(timeout=60)
        status_cb(f'正在下载 Forge {forge_version} 安装器...')
        emit('loader_file', 0)
        _download_installer(_forge_installer_urls(mc_version, forge_version, official_first=mode == 'B'), installer, http=http, status_cb=status_cb, cancel=cancel, progress_cb=(lambda p: emit('loader_file', p * 0.2)))
        emit('loader_file', 20)
        update_progress('prepare', 15)
        profile = _read_forge_profile(installer)
        prepare_result['profile'] = profile
        status_cb('正在通过 BMCLAPI 预下载 Forge 依赖...')
        def prefetch_progress(p):
            update_progress('prepare', 15 + int(p * 0.85))
            emit('loader_file', 20 + p * 0.8)

        prefetch_installer_libraries(installer, mc_dir, status_cb=status_cb, progress_cb=prefetch_progress, cancel=cancel)
        update_progress('prepare', 100)
        emit('loader_file', 100)

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        runtime_future = pool.submit(install_runtime)
        prepare_future = pool.submit(prepare_installer)
        for future in runtime_future, prepare_future:
            try:
                future.result()
            except (InstallCancelled, DownloadCancelled):
                raise
            except Exception as exc:
                errors.append(exc)
                continue
    if errors:
        raise DownloadError(f'Forge {forge_version} 运行环境准备失败：{errors[0]}') from errors[0]
    if progress_cb:
        progress_cb(50)
    profile = prepare_result.get('profile') or _read_forge_profile(installer)
    _prefetch_mojmaps(profile, mc_dir, status_cb=status_cb, cancel=cancel)
    http = HttpClient(timeout=60)
    generated = None
    log_path = None
    legacy = _profile_is_legacy_forge(profile)
    if legacy:
        status_cb('安装方式 A：旧版 Forge 兼容安装流程...')
        emit('loader_install', 5)
        try:
            generated = _install_legacy_forge(installer, profile, mc_dir_path, mc_version, forge_version, status_cb=status_cb, cancel=cancel)
            emit('loader_install', 100)
        except (InstallCancelled, DownloadCancelled):
            raise
        except Exception as exc:
            if mode == 'A':
                raise
            status_cb(f'安装方式 A 失败（{exc}），正在尝试安装方式 B：官方安装器...')
            log_cb(f'Forge 旧版安装方式 A 失败: {exc}')
            _download_installer(_forge_installer_urls(mc_version, forge_version, official_first=True), installer, http=http, status_cb=status_cb, cancel=cancel)
            profile = _read_forge_profile(installer)
            if not _profile_is_legacy_forge(profile):
                raise DownloadError('官方 Forge 安装器格式与镜像不一致，无法继续旧版兼容安装')
            emit('loader_install', 5)
            generated = _install_legacy_forge(installer, profile, mc_dir_path, mc_version, forge_version, status_cb=status_cb, cancel=cancel)
            emit('loader_install', 100)
        ProgressMapper(50, 10, progress_cb)(100)
    else:
        java_path = pick_java(mc_version)
        log_path = LOG_DIR / f'install_{mc_version}-{forge_version}.log'
        patched = _patch_installer_offline(installer)
        rc = 1
        if mode in ('AUTO', 'A'):
            status_cb('安装方式 A：预下载依赖 + 离线安装...')
            emit('loader_install', 5)
            rc = _run_installer(java_path, patched, ['--installClient', str(mc_dir), '--offline'], mc_dir_path, status_cb=status_cb, log_path=log_path, maven_mirror=False, cancel=cancel)
            if rc == 0:
                emit('loader_install', 100)
                ProgressMapper(50, 10, progress_cb)(100)
        if rc != 0 and mode != 'A':
            status_cb('安装方式 B：镜像在线安装...')
            _prefetch_mojmaps(profile, mc_dir, status_cb=status_cb, cancel=cancel)
            log_path = LOG_DIR / f'install_{mc_version}-{forge_version}_online.log'
            emit('loader_install', 5)
            rc = _run_installer(java_path, patched, ['--installClient', str(mc_dir), '--mirror', 'https://repo.cleanroommc.com/releases/'], mc_dir_path, status_cb=status_cb, log_path=log_path, maven_mirror=False, cancel=cancel)
            if rc == 0:
                emit('loader_install', 100)
                ProgressMapper(50, 10, progress_cb)(100)
        if rc != 0:
            if log_path:
                raise DownloadError(f'Forge 安装器返回错误码 {rc}：\n{_tail_log(log_path)}')
            raise DownloadError(f'Forge 安装器返回错误码 {rc}')
        generated = find_version_dir(mc_dir_path, mc_version, 'forge', before)
    if generated is None:
        for name in f'{mc_version}-forge-{forge_version}', f'{mc_version}-Forge-{forge_version}', f'{mc_version}-{forge_version}':
            if (versions_dir / name).exists():
                generated = versions_dir / name
                break
    if generated is None:
        if log_path:
            raise DownloadError('Forge 安装器未生成版本目录，请查看日志：' + str(log_path))
        raise DownloadError('Forge 安装器未生成版本目录，请查看日志：' + str(LOG_DIR))
    log_cb(f'Forge 安装完成: {generated.name}')
    _install_patched_client(profile, mc_dir_path, generated, generated.name)
    parent_jar = generated / f'{generated.name}.jar'
    jar_id = generated.name if parent_jar.exists() else mc_version
    _mark_mc_version(generated, mc_version)
    if custom_name != generated.name:
        create_alias(mc_dir, custom_name, generated.name, jar_id=jar_id)
        _mark_mc_version(versions_dir / custom_name, mc_version)
    else:
        log_cb(f'版本名与安装器名称一致，直接使用 {custom_name}')
    emit('game', 100)
    status_cb('正在校验 Forge 实例...')
    def final_phase(category, percent):
        key = {'libraries': 'game_libs', 'assets': 'assets', 'json': 'json'}.get(category)
        if key:
            emit(key, percent)

    engine = RepairEngine(custom_name, mc_dir, status=status_cb, progress=ProgressMapper(60, 40, progress_cb), phase=final_phase, log=log_cb, cancel=cancel, fast=True)
    result = engine.repair()
    if result.failed:
        raise DownloadError('Forge 依赖下载不完整：' + '、'.join(result.failed_items[:5]))
    return custom_name

def _neoforge_installer_supports_client(installer):
    try:
        with zipfile.ZipFile(installer) as zf:
            for name in zf.namelist():
                if name.endswith('SimpleInstaller.class'):
                    return b'install-client' in zf.read(name)
            return False
    except Exception:
        return False


def _mark_mc_version(version_dir, mc_version):
    '''在版本 JSON 中记录真实原版 MC 版本，供“恢复原版”等流程使用。'''

    try:
        version_dir = Path(version_dir)
        json_path = version_dir / f'{version_dir.name}.json'
        data = {}
        if json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                data = {}
        if not isinstance(data, dict) or not data:
            return
        if not data.get('pmgl_mc'):
            data['pmgl_mc'] = str(mc_version)
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
            )
    except (OSError, ValueError):
        pass

def _find_newer_neoforge(mc_version, current):
    try:
        http = HttpClient(timeout=20)
        data = http.get_json(f'{BMCLAPI}/neoforge/list/{mc_version}')
        versions = []
        for item in data or []:
            if not isinstance(item, dict):
                continue
            ver = str(item.get('version') or '')
            if ver and ver != current:
                versions.append(ver)
        return list(reversed(versions[-20:]))
    except Exception:
        return []

def _neoforge_installer_urls(mc_version, neoforge_version, official_first=False):
    mirror = f'{BMCLAPI}/neoforge/version/{neoforge_version}/download/installer.jar'
    fallback = f'{OPENBMCLAPI}/neoforge/version/{neoforge_version}/download/installer.jar'
    extra = build_neoforge_installer_urls(mc_version, neoforge_version, prefer_official=official_first)
    if official_first:
        candidates = extra + [mirror, fallback]
    else:
        candidates = [mirror] + extra + [fallback]
    result = []
    for url in candidates:
        if url and url not in result:
            result.append(url)
    return result

def install_neoforge(mc_version, neoforge_version, mc_dir, custom_name, *, method='auto', status_cb=(lambda _s: None), progress_cb=None, phase_cb=None, log_cb=(lambda _s: None), cancel=(lambda: False)):
    mode = (method or 'auto').strip().upper()
    emit = phase_cb or (lambda _k, _p: None)
    mc_dir_path = Path(mc_dir)
    versions_dir = mc_dir_path / 'versions'
    status_cb(f'正在准备 NeoForge {neoforge_version} 运行环境...')
    def vanilla_phase(category, percent):
        key = {'libraries': 'libs', 'assets': 'assets', 'json': 'json'}.get(category)
        if key:
            emit(key, percent)

    progress_state = {'vanilla': 0, 'prepare': 0}
    progress_lock = threading.Lock()
    def update_progress(key, value):
        with progress_lock:
            progress_state[key] = max(progress_state[key], max(0, min(100, value)))
            combined = int(progress_state['vanilla'] * 0.25 + progress_state['prepare'] * 0.25)
        if progress_cb:
            progress_cb(min(50, combined))

    temp_dir = PROJECT_TEMP / 'neoforge_installer'
    temp_dir.mkdir(exist_ok=True)
    installer = temp_dir / f'neoforge-installer-{neoforge_version}.jar'
    http = HttpClient(timeout=60)
    prepare_result = {'profile': {}, 'version': neoforge_version, 'installer': installer}
    def install_runtime():
        install_vanilla(mc_version, mc_dir, status_cb=status_cb, progress_cb=(lambda p: update_progress('vanilla', p)), phase_cb=vanilla_phase, log_cb=log_cb, cancel=cancel)

    def prepare_installer():
        nonlocal neoforge_version, installer
        status_cb(f'正在下载 NeoForge {neoforge_version} 安装器...')
        emit('loader_file', 0)
        _download_installer(_neoforge_installer_urls(mc_version, neoforge_version, official_first=mode == 'B'), installer, http=http, status_cb=status_cb, cancel=cancel, progress_cb=(lambda p: emit('loader_file', p * 0.15)))
        emit('loader_file', 15)
        update_progress('prepare', 15)
        if not _neoforge_installer_supports_client(installer):
            status_cb('当前 NeoForge 安装器不支持命令行客户端安装，正在查找较新版本...')
            replaced = False
            for candidate in _find_newer_neoforge(mc_version, neoforge_version):
                cand_installer = temp_dir / f'neoforge-installer-{candidate}.jar'
                try:
                    _download_installer(_neoforge_installer_urls(mc_version, candidate), cand_installer, http=http, status_cb=status_cb, cancel=cancel)
                except Exception:
                    pass
                else:
                    if _neoforge_installer_supports_client(cand_installer):
                        log_cb(f'NeoForge {neoforge_version} 安装器过旧，自动改用 {candidate}')
                        status_cb(f'已自动改用 NeoForge {candidate}')
                        neoforge_version = candidate
                        installer = cand_installer
                        replaced = True
                        break
            if not replaced:
                raise DownloadError('所选 NeoForge 版本过旧，其安装器不支持自动客户端安装。\n请在版本列表中改选较新的 NeoForge 版本。')
        profile = {}
        try:
            with zipfile.ZipFile(installer) as zf:
                if 'install_profile.json' in zf.namelist():
                    profile = json.loads(zf.read('install_profile.json').decode('utf-8'))
        except Exception:
            profile = {}
        prepare_result['profile'] = profile
        prepare_result['version'] = neoforge_version
        prepare_result['installer'] = installer
        status_cb('正在通过 BMCLAPI 预下载 NeoForge 依赖...')
        def prefetch_progress(p):
            update_progress('prepare', 15 + int(p * 0.85))
            emit('loader_file', 15 + p * 0.85)

        prefetch_installer_libraries(installer, mc_dir, status_cb=status_cb, progress_cb=prefetch_progress, cancel=cancel)
        update_progress('prepare', 100)
        emit('loader_file', 100)

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        runtime_future = pool.submit(install_runtime)
        prepare_future = pool.submit(prepare_installer)
        for future in runtime_future, prepare_future:
            try:
                future.result()
            except (InstallCancelled, DownloadCancelled):
                raise
            except Exception as exc:
                errors.append(exc)
                continue
    if errors:
        raise DownloadError(f'NeoForge {neoforge_version} 运行环境准备失败：{errors[0]}') from errors[0]
    profile = prepare_result.get('profile') or {}
    neoforge_version = prepare_result.get('version') or neoforge_version
    installer = prepare_result.get('installer') or installer
    if progress_cb:
        progress_cb(50)
    before = {p.name for p in versions_dir.iterdir() if p.is_dir()} if versions_dir.exists() else set()
    java_path = pick_java(mc_version)
    log_path = LOG_DIR / f'install_{neoforge_version}.log'
    patched = _patch_installer_offline(installer)
    rc = 1
    if mode in ('AUTO', 'A'):
        status_cb('安装方式 A：预下载依赖 + 离线安装...')
        emit('loader_install', 5)
        rc = _run_installer(java_path, patched, ['--install-client', str(mc_dir), '--offline'], mc_dir_path, status_cb=status_cb, log_path=log_path, maven_mirror=False, cancel=cancel)
        if rc == 0:
            emit('loader_install', 100)
            ProgressMapper(50, 10, progress_cb)(100)
    if rc != 0 and mode != 'A':
        status_cb('安装方式 B：镜像在线安装...')
        _prefetch_mojmaps(profile, mc_dir, status_cb=status_cb, cancel=cancel)
        log_path = LOG_DIR / f'install_{neoforge_version}_online.log'
        emit('loader_install', 5)
        rc = _run_installer(java_path, patched, ['--install-client', str(mc_dir), '--mirror', 'https://repo.cleanroommc.com/releases/'], mc_dir_path, status_cb=status_cb, log_path=log_path, maven_mirror=False, cancel=cancel)
        if rc == 0:
            emit('loader_install', 100)
            ProgressMapper(50, 10, progress_cb)(100)
    if rc != 0:
        raise DownloadError(f'NeoForge 安装器返回错误码 {rc}：\n{_tail_log(log_path)}')
    ProgressMapper(50, 10, progress_cb)(100)
    generated = find_version_dir(mc_dir_path, mc_version, 'neoforge', before)
    if generated is None:
        forge_ver = neoforge_version
        prefix = f'{mc_version}-'
        if forge_ver.startswith(prefix):
            forge_ver = forge_ver[len(prefix):]
        for name in f'neoforge-{neoforge_version}', f'{mc_version}-neoforge-{neoforge_version}', f'{mc_version}-forge-{forge_ver}', f'neoforge-{forge_ver}':
            if (versions_dir / name).exists():
                generated = versions_dir / name
                break
    if generated is None:
        raise DownloadError('NeoForge 安装器未生成版本目录，请查看日志：' + str(log_path))
    log_cb(f'NeoForge 安装完成: {generated.name}')
    _install_patched_client(profile, mc_dir_path, generated, generated.name)
    parent_jar = generated / f'{generated.name}.jar'
    jar_id = generated.name if parent_jar.exists() else mc_version
    _mark_mc_version(generated, mc_version)
    if custom_name != generated.name:
        create_alias(mc_dir, custom_name, generated.name, jar_id=jar_id)
        _mark_mc_version(versions_dir / custom_name, mc_version)
    else:
        log_cb(f'版本名与安装器名称一致，直接使用 {custom_name}')
    emit('game', 100)
    status_cb('正在校验 NeoForge 实例...')
    def final_phase(category, percent):
        key = {'libraries': 'game_libs', 'assets': 'assets', 'json': 'json'}.get(category)
        if key:
            emit(key, percent)

    engine = RepairEngine(custom_name, mc_dir, status=status_cb, progress=ProgressMapper(60, 40, progress_cb), phase=final_phase, log=log_cb, cancel=cancel, fast=True)
    result = engine.repair()
    if result.failed:
        raise DownloadError('NeoForge 依赖下载不完整：' + '、'.join(result.failed_items[:5]))
    return custom_name

# WARNING: Decompyle incomplete
