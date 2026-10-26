'''
启动核心模块 - 完整 LaunchManager 和安全终止的 LaunchWorker
修正：避免 _process 为 None 时调用 wait
'''

import subprocess
import os
import platform
import re
import shutil
import sys
import json
import logging
import threading
import time
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import QThread, Signal
import minecraft_launcher_lib
from core.config import ConfigManager
from core.auth import AuthManager
logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger('LaunchManager')
_SHARED_JAVA_LIST = None
_JAVA_LIST_LOCK = threading.Lock()

def _library_applies(lib: dict) -> bool:
    '''按当前系统/架构判断 library 是否可用。

    合并 inheritsFrom 链时必须先过滤不适用于当前系统的条目，
    否则 osx 专用同名库会覆盖 windows 可用的 lwjgl 条目。
    '''

    rules = lib.get('rules')
    if not rules:
        return True
    os_name = 'windows' if platform.system() == 'Windows' else 'osx' if platform.system() == 'Darwin' else 'linux'
    is_64 = platform.architecture()[0] == '64bit'
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

def parse_placeholder(text: str, ctx: dict) -> str:
    if not text:
        return ''
    now = datetime.now()
    time_ctx = {'{date}': now.strftime('%Y/%m/%d'), '{time}': now.strftime('%H:%M:%S')}
    full_ctx = {**time_ctx, **ctx}
    result = text
    for tag, value in full_ctx.items():
        result = result.replace(tag, str(value))
    return result

class LaunchManager:
    def __init__(self):
        self.config = ConfigManager()
        self.auth = AuthManager()
        self._cached_java_list = None
        self.LAUNCHER_NAME = 'SCL'
        self.LAUNCHER_VERSION = '1.1.0'
        self.last_window_title = ''

    def detect_java(self) -> str:
        user_java = self.config.get('java_path', '')
        if user_java and os.path.isfile(user_java):
            return user_java
        try:
            java_path = minecraft_launcher_lib.utils.get_java_executable()
            if java_path:
                return java_path
            return ''
        except Exception:
            return ''

    def validate_java(self, java_path: str) -> tuple[bool, str]:
        if not java_path or not os.path.isfile(java_path):
            return (False, 'Java 路径无效')
        try:
            result = subprocess.run([java_path, '-version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                version_info = result.stderr or result.stdout
                if version_info:
                    return True, version_info.split('\n')[0]
                return True, '可用'
            return (False, 'Java 执行失败')
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return (False, 'Java 无法执行')

    def get_cached_java_list(self):
        global _SHARED_JAVA_LIST
        if self._cached_java_list is not None:
            return self._cached_java_list
        with _JAVA_LIST_LOCK:
            shared = _SHARED_JAVA_LIST
        if shared is None:
            # 扫描不持锁进行：扫描过程中会调用 _get_java_version → 读取缓存，
            # 若持锁会导致扫描线程自锁、界面永久卡死
            shared = self._detect_all_java()
            with _JAVA_LIST_LOCK:
                _SHARED_JAVA_LIST = shared
        self._cached_java_list = shared
        return self._cached_java_list

    def peek_cached_java_list(self):
        '''不触发扫描，返回当前已检测到的 Java 列表；尚未完成时返回 None。'''

        with _JAVA_LIST_LOCK:
            if _SHARED_JAVA_LIST is None:
                return None
            self._cached_java_list = list(_SHARED_JAVA_LIST)
            return list(_SHARED_JAVA_LIST)

    def refresh_java_list(self):
        global _SHARED_JAVA_LIST
        result = self._detect_all_java()
        with _JAVA_LIST_LOCK:
            _SHARED_JAVA_LIST = result
            self._cached_java_list = result
        return result

    def _get_required_java_version(self, minecraft_version):
        from core.java_requirements import recommended_java_major
        mc_dir = Path(self.config.get_mc_dir())
        version_root = mc_dir / 'versions'
        try:
            pack = json.loads((version_root / minecraft_version / 'pmgl_pack.json').read_text(encoding='utf-8'))
            base = str(pack.get('mc_version') or '').strip()
            if base:
                recommended = recommended_java_major(base)
                if recommended:
                    return recommended
        except (OSError, ValueError, TypeError):
            pass
        chain = []
        current = minecraft_version
        seen = set()
        while current and current not in seen and len(chain) < 10:
            seen.add(current)
            data = {}
            try:
                data = json.loads((version_root / current / f'{current}.json').read_text(encoding='utf-8'))
            except (OSError, ValueError, TypeError):
                data = {}
            chain.append((current, data))
            parent = data.get('inheritsFrom')
            if not parent:
                break
            current = parent
        majors = []
        for _version_id, data in chain:
            try:
                major = (data.get('javaVersion') or {}).get('majorVersion')
                if major:
                    majors.append(int(major))
            except (TypeError, ValueError):
                continue
        if majors:
            return max(majors)
        if chain:
            recommended = recommended_java_major(chain[-1][0])
            if recommended:
                return recommended
        recommended = recommended_java_major(minecraft_version)
        if recommended:
            return recommended
        parts = minecraft_version.split('.')
        if len(parts) >= 1:
            try:
                major = int(parts[0])
                if major >= 26:
                    return 25
                if major >= 21:
                    return 21
                if major >= 18:
                    return 17
                if major >= 17:
                    return 16
            except ValueError:
                pass
        return 8

    def _detect_all_java(self):
        java_list = []
        seen_paths = set()

        def add_candidate(full):
            if not full:
                return
            try:
                key = os.path.normcase(os.path.abspath(str(full)))
            except Exception:
                return
            if key in seen_paths or not os.path.isfile(full):
                return
            seen_paths.add(key)
            ver = self._get_java_version(full)
            if ver:
                java_list.append((ver, str(full)))

        # 1) `where java` / PATH
        for exe_name in ('java.exe', 'javaw.exe'):
            found = shutil.which(exe_name)
            if found:
                add_candidate(found)
                javaw = os.path.join(os.path.dirname(found), 'javaw.exe')
                if os.path.isfile(javaw):
                    add_candidate(javaw)
        for d in os.environ.get('PATH', '').split(os.pathsep):
            for exe in ('javaw.exe', 'java.exe'):
                add_candidate(os.path.join(d, exe))

        # 2) 常见安装目录（所有固定盘符）
        # 即使 PATH 已命中也要继续扫描：PATH 通常只指向一个 Java（往往是过新的版本），
        # 找不到旧版本 Minecraft/Forge 需要的 Java 17。
        sub_dirs = (
            'Program Files\\Java',
            'Program Files (x86)\\Java',
            'Program Files\\Eclipse Adoptium',
            'Program Files\\Eclipse Foundation',
            'Program Files\\AdoptOpenJDK',
            'Program Files\\Microsoft',
            'Program Files\\Zulu',
            'Program Files\\BellSoft',
            'Program Files\\Amazon Corretto',
            'Program Files\\Semeru',
            'Program Files\\IBM\\Semeru',
            'Program Files\\GraalVM',
            'Program Files\\Android\\Android Studio\\jbr',
            'Program Files\\JetBrains',
            'Java',
            '.jdks',
            'Environment\\Java',
        )
        drive_roots = []
        java_home = os.environ.get('JAVA_HOME', '')
        if java_home:
            drive_roots.append(java_home)
        for drive in self._fixed_drive_letters():
            for sub in sub_dirs:
                drive_roots.append(f'{drive}:\\{sub}')
        for base in drive_roots:
            if not os.path.isdir(base):
                continue
            try:
                for root, dirs, files in os.walk(base):
                    depth = root[len(base):].count(os.sep)
                    if depth >= 4:
                        dirs[:] = []
                    if 'javaw.exe' in files:
                        add_candidate(os.path.join(root, 'javaw.exe'))
                    elif 'java.exe' in files:
                        add_candidate(os.path.join(root, 'java.exe'))
            except OSError:
                continue
        # 3) 启动器自带/官方自动下载的运行时
        try:
            runtime_dir = Path(self.config.get_mc_dir()) / 'runtime'
            if runtime_dir.is_dir():
                for exe in runtime_dir.rglob('javaw.exe'):
                    add_candidate(str(exe))
        except Exception:
            pass
        try:
            from core.paths import scl_dir
            launcher_runtime = scl_dir() / 'runtime'
            if launcher_runtime.is_dir():
                for exe in launcher_runtime.rglob('javaw.exe'):
                    add_candidate(str(exe))
        except Exception:
            pass
        return java_list

    @staticmethod
    def _fixed_drive_letters():
        '''返回本机固定磁盘盘符（跳过高延迟的网络盘/光驱）。'''

        if sys.platform != 'win32':
            return []
        try:
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            drive_type = ctypes.windll.kernel32.GetDriveTypeW
            letters = []
            for i in range(26):
                if not (bitmask >> i) & 1:
                    continue
                letter = chr(ord('A') + i)
                # 3 = DRIVE_FIXED；跳过网络盘(4)/光驱(5)/可移动(2)避免长时间枚举
                if drive_type(f'{letter}:\\') == 3:
                    letters.append(letter)
            return letters
        except Exception:
            return []

    def _get_java_version(self, java_path):
        cached = self._cached_java_version(java_path)
        if cached is not None:
            return cached
        try:
            result = subprocess.run([java_path, '-version'], capture_output=True, text=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            output = result.stderr + result.stdout
            match = re.search('version "(\\d+\\.\\d+\\.\\d+[^"]*)"', output)
            if match:
                return match.group(1)
            match2 = re.search('(\\d+\\.\\d+\\.\\d+[^\\s]*)', output)
            if match2:
                return match2.group(1)
        except Exception:
            pass
        return ''

    def _cached_java_version(self, java_path):
        '''从已完成的 Java 扫描缓存里取版本，避免同步运行 java -version 卡界面。'''

        if not java_path:
            return None
        try:
            key = os.path.normcase(os.path.abspath(str(java_path)))
        except Exception:
            return None
        # 直接读取全局列表引用（CPython 原子），不能走加锁接口：
        # 扫描线程自身正是通过这里查询缓存，加锁会死锁
        shared = list(_SHARED_JAVA_LIST or [])
        for ver, path in shared:
            try:
                if os.path.normcase(os.path.abspath(str(path))) == key:
                    return str(ver)
            except Exception:
                continue
        return None

    @staticmethod
    def _parse_java_major(version_text) -> int | None:
        text = str(version_text or '').strip()
        if not text:
            return None
        try:
            if text.startswith('1.'):
                return int(text.split('.')[1])
            return int(text.split('.')[0])
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _java_arch_rank(java_path) -> int:
        '''0=64 位，1=未知，2=32 位；用于优先选择 64 位 Java。'''

        try:
            with open(java_path, 'rb') as fh:
                if fh.read(2) != b'MZ':
                    return 1
                fh.seek(60)
                offset = int.from_bytes(fh.read(4), 'little')
                fh.seek(offset)
                if fh.read(4) != b'PE\x00\x00':
                    return 1
                machine = int.from_bytes(fh.read(2), 'little')
            if machine in (34404, 43620):
                return 0
            if machine == 332:
                return 2
            return 1
        except OSError:
            return 1

    def _select_best_java(self, required_version, max_version=None):
        if self._cached_java_list is None:
            self._cached_java_list = self.get_cached_java_list()
        java_list = self._cached_java_list or []
        candidates = []
        for ver_str, path in java_list:
            major = self._parse_java_major(ver_str)
            if major is None or major < required_version:
                continue
            if max_version is not None and major > max_version:
                continue
            candidates.append((major, self._java_arch_rank(path), 0 if str(path).lower().endswith('javaw.exe') else 1, ver_str, path))
        if not candidates:
            return (None, None)
        candidates.sort(key=(lambda x: (x[0], x[1], x[2])))
        return candidates[0][3], candidates[0][4]

    def _get_base_mc_version(self, final_id: str) -> str:
        '''取得用于查询 Java 要求的原版 MC 版本号。'''

        mc_dir = Path(self.config.get_mc_dir())
        version_root = mc_dir / 'versions'
        try:
            pack = json.loads((version_root / final_id / 'pmgl_pack.json').read_text(encoding='utf-8'))
            base = str(pack.get('mc_version') or '').strip()
            if base:
                return base
        except (OSError, ValueError, TypeError):
            pass
        chain = []
        current = final_id
        seen = set()
        while current and current not in seen and len(chain) < 10:
            seen.add(current)
            data = {}
            try:
                data = json.loads((version_root / current / f'{current}.json').read_text(encoding='utf-8'))
            except (OSError, ValueError, TypeError):
                data = {}
            chain.append((current, data))
            parent = data.get('inheritsFrom')
            if not parent:
                break
            current = parent
        if chain:
            base = str(chain[-1][0] or '').strip()
            if re.match('^\\d+\\.\\d+', base):
                return base
        return final_id

    def _get_java_bounds(self, final_id: str, required: int):
        '''返回 (最低主版本, 最高主版本或 None, 是否有明确上限)。'''

        from core.java_requirements import java_major_bounds
        bounds = java_major_bounds(self._get_base_mc_version(final_id))
        if bounds:
            return int(bounds[0]), int(bounds[1]), True
        return int(required), None, False

    def preflight_java(self, version_id: str) -> dict:
        '''启动前检查 Java，不修改任何设置，供主线程弹窗使用。'''

        mc_dir = self.config.get_mc_dir()
        actual_id, _version_dir = self._resolve_version_id(version_id, mc_dir)
        final_id = actual_id or version_id
        required = self._get_required_java_version(final_id)
        min_major, max_major, has_max = self._get_java_bounds(final_id, required)
        auto_max = max_major if has_max else None
        version_settings = self.get_version_settings(version_id)
        candidates = [version_settings.get('java_path', ''), self.config.get('java_path', '')]
        selected_path = ''
        selected_major = None
        for candidate in candidates:
            candidate = str(candidate or '').strip()
            if not candidate or not os.path.isfile(candidate):
                continue
            major = self._parse_java_major(self._get_java_version(candidate))
            if major is None:
                continue
            selected_path = candidate
            selected_major = major
        def auto_choice():
            version_text, path = self._select_best_java(required, auto_max)
            major = self._parse_java_major(version_text)
            return version_text, path, major

        result = {'version_id': version_id, 'final_id': final_id, 'required': required, 'min_major': min_major, 'max_major': max_major, 'has_max': has_max, 'selected_path': selected_path, 'selected_major': selected_major, 'auto_path': '', 'auto_major': None, 'using_auto': False}
        if selected_major is not None:
            too_low = selected_major < min_major
            too_high = bool(has_max and selected_major > max_major)
            if too_low or too_high:
                auto_version, auto_path, auto_major = auto_choice()
                result.update({'status': 'mismatch', 'auto_path': auto_path, 'auto_major': auto_major, 'auto_version': auto_version})
                return result
            result['status'] = 'ok'
            return result
        auto_version, auto_path, auto_major = auto_choice()
        if auto_path:
            result.update({'status': 'ok', 'selected_path': auto_path, 'selected_major': auto_major, 'auto_path': auto_path, 'auto_major': auto_major, 'auto_version': auto_version, 'using_auto': True})
            return result
        result['status'] = 'missing'
        return result

    def apply_auto_java(self, version_id: str, java_path: str) -> bool:
        if not java_path or not os.path.isfile(java_path):
            return False
        version_settings = dict(self.get_version_settings(version_id) or {})
        version_settings['java_path'] = java_path
        self.save_version_settings(version_id, version_settings)
        return True

    def get_version_settings(self, version_id: str) -> dict:
        version_settings = self.config.get('version_settings', {})
        return version_settings.get(version_id, {})

    def get_pre_launch_command(self, version_id: str) -> str:
        settings = self.get_version_settings(version_id)
        value = settings.get('pre_launch_command')
        if value is None or value == '':
            value = self.config.get('pre_launch_command', '')
        return str(value or '').strip()

    def is_version_isolated(self, version_id: str) -> bool:
        settings = self.get_version_settings(version_id)
        if 'isolated' in settings:
            return bool(settings.get('isolated'))
        # 与 PCL/HMCL 互通：本地无显式设置时，读取外部启动器的隔离标记
        try:
            from core.interop import read_external_isolation
            version_dir = Path(self.config.get_mc_dir()) / 'versions' / version_id
            external = read_external_isolation(version_dir)
            if external is not None:
                return external
        except Exception:
            pass
        policy = str(self.config.get('default_isolation', 'off') or 'off').lower()
        if policy in ('', 'off', 'none'):
            return False
        if policy == 'all':
            return True
        version_dir = Path(self.config.get_mc_dir()) / 'versions' / version_id
        data = {}
        try:
            data = json.loads((version_dir / f'{version_id}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        name = version_id.lower()
        is_modded = any((key in name for key in ('forge', 'fabric', 'neoforge', 'quilt', 'optifine'))) or (version_dir / 'pmgl_pack.json').exists()
        if not is_modded:
            try:
                blob = json.dumps(data, ensure_ascii=False).lower()
                is_modded = any((marker in blob for marker in ('fabric-loader', 'net.fabricmc', 'quilt-loader', 'org.quiltmc', 'minecraftforge', 'net.minecraftforge', 'bootstraplauncher', 'net.neoforged', 'neoforge', 'optifine')))
            except Exception:
                pass
        is_release = str(data.get('type', 'release')).lower() == 'release'
        if policy == 'modded':
            return is_modded
        if policy == 'non_release':
            return not is_release
        if policy == 'modded_non_release':
            return is_modded or not is_release
        return False

    def _migrate_legacy_isolated_dir(self, version_id: str):
        '''把旧版隔离目录 versions/<id>/game 的内容迁移到 versions/<id>。'''

        version_dir = Path(self.config.get_mc_dir()) / 'versions' / version_id
        legacy = version_dir / 'game'
        if not legacy.is_dir():
            return
        def merge(src: Path, dst: Path):
            if not dst.exists():
                try:
                    shutil.move(str(src), str(dst))
                    return
                except OSError:
                    pass
            if src.is_dir():
                if dst.is_dir():
                    for child in list(src.iterdir()):
                        merge(child, dst / child.name)
                    try:
                        src.rmdir()
                    except OSError:
                        pass

        for child in list(legacy.iterdir()):
            merge(child, version_dir / child.name)
        try:
            legacy.rmdir()
        except OSError:
            pass

    def get_game_dir(self, version_id: str) -> Path:
        '''获取版本实际使用的游戏目录（与启动参数保持一致）。'''

        mc_dir = Path(self.config.get_mc_dir())
        if not version_id:
            return mc_dir
        if self.is_version_isolated(version_id):
            self._migrate_legacy_isolated_dir(version_id)
            return mc_dir / 'versions' / version_id
        return mc_dir

    def get_mods_dir(self, version_id: str) -> Path:
        return self.get_game_dir(version_id) / 'mods'

    def get_resourcepacks_dir(self, version_id: str) -> Path:
        return self.get_game_dir(version_id) / 'resourcepacks'

    def get_shaderpacks_dir(self, version_id: str) -> Path:
        return self.get_game_dir(version_id) / 'shaderpacks'

    def get_saves_dir(self, version_id: str) -> Path:
        return self.get_game_dir(version_id) / 'saves'

    def save_version_settings(self, version_id: str, settings: dict):
        version_settings = self.config.get('version_settings', {})
        version_settings[version_id] = settings
        self.config.set('version_settings', version_settings)
        self.config.save()

    def _format_mem(self, mem_str):
        mem_str = mem_str.strip().upper()
        match = re.match('^([\\d.]+)([GMK]?)$', mem_str)
        if not match:
            return '2G'
        value, unit = match.groups()
        try:
            if '.' in value:
                int_val = int(float(value))
            else:
                int_val = int(value)
        except:
            int_val = 2
        if not unit:
            unit = 'G'
        return f'{int_val}{unit}'

    def _resolve_version_id(self, version_id, mc_dir):
        versions_path = Path(mc_dir) / 'versions'
        if not versions_path.exists():
            return (None, None)
        target_dir = versions_path / version_id
        if target_dir.exists():
            if not self._fix_json(target_dir, version_id, mc_dir):
                return (None, None)
            return version_id, target_dir
        for item in versions_path.iterdir():
            if item.is_dir() and item.name.startswith(version_id):
                if not self._fix_json(item, item.name, mc_dir):
                    continue
                return item.name, item
        return (None, None)

    @staticmethod
    def _looks_like_version_json(data) -> bool:
        if not isinstance(data, dict):
            return False
        return any((key in data for key in ('libraries', 'mainClass', 'inheritsFrom', 'assetIndex', 'jar', 'clientVersion', 'downloads', 'minecraftArguments')))

    def _fix_json(self, version_dir, expected_id, mc_dir):
        if not version_dir.exists():
            return False
        expected_path = version_dir / f'{expected_id}.json'
        json_path = None
        data = None
        if expected_path.exists():
            try:
                candidate = json.loads(expected_path.read_text(encoding='utf-8'))
                if self._looks_like_version_json(candidate):
                    json_path = expected_path
                    data = candidate
            except (OSError, ValueError):
                pass
        if json_path is None:
            for candidate_path in version_dir.glob('*.json'):
                if candidate_path == expected_path:
                    continue
                try:
                    candidate = json.loads(candidate_path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
                else:
                    if not self._looks_like_version_json(candidate):
                        continue
                    json_path = candidate_path
                    data = candidate
        if json_path is None or data is None:
            return False
        inherit_id = data.get('inheritsFrom')
        if inherit_id:
            inherit_dir = Path(mc_dir) / 'versions' / inherit_id
            if not inherit_dir.exists():
                logger.error(f'依赖原版目录 {inherit_id} 不存在，请检查安装是否完整。')
                return False
        try:
            if data.get('id') != expected_id:
                logger.info(f'修正 JSON id: {data.get("id")} -> {expected_id}')
                data['id'] = expected_id
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            if json_path.name != expected_path.name:
                if expected_path.exists():
                    logger.error(f'版本 JSON 目标已存在且不是有效版本文件，已停止覆盖: {expected_path.name}')
                    return False
                logger.info(f'重命名 JSON: {json_path.name} -> {expected_path.name}')
                json_path.rename(expected_path)
            return True
        except Exception as e:
            logger.error(f'修正 JSON 失败: {e}')
            return False

    def _load_merged_version(self, version_id: str, mc_dir: str) -> dict:
        '''递归合并 inheritsFrom 链，Forge/整合包别名也能拿到原版库和参数。'''

        versions_dir = Path(mc_dir) / 'versions'
        chain = []
        current = version_id
        seen = set()
        while current and current not in seen and len(chain) < 10:
            seen.add(current)
            json_path = versions_dir / current / f'{current}.json'
            if not json_path.is_file():
                raise FileNotFoundError(f'缺少版本 JSON: {json_path}')
            data = json.loads(json_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError(f'版本 JSON 无效: {json_path}')
            chain.append((current, data))
            parent = str(data.get('inheritsFrom') or '')
            if not parent:
                break
            current = parent
        if not chain:
            raise FileNotFoundError(f'版本 {version_id} 不存在')
        merged = {}
        libraries = {}
        jvm_args = []
        game_args = []
        legacy_tokens = []
        for _dir_id, data in reversed(chain):
            for key, value in data.items():
                if key in ('libraries', 'arguments', 'minecraftArguments', 'inheritsFrom', 'id', 'jar', 'mainClass'):
                    continue
                merged[key] = value
            for lib in data.get('libraries') or []:
                if not (isinstance(lib, dict) and lib.get('name')):
                    continue
                if not _library_applies(lib):
                    continue
                name = str(lib['name'])
                parts = name.split('@', 1)[0].split(':')
                key = ':'.join(parts[:2])
                if len(parts) > 3:
                    key += ':' + parts[3]
                if key in libraries:
                    libraries.pop(key)
                libraries[key] = lib
            arguments = data.get('arguments')
            if isinstance(arguments, dict):
                jvm_args.extend(arguments.get('jvm') or [])
                game_args.extend(arguments.get('game') or [])
            if data.get('minecraftArguments'):
                # 链上各层的 legacy 参数拼接去重（父版本在前），
                # 避免 OptiFine 这类子版本覆盖掉 Forge 的 FMLTweaker 参数
                for token in str(data['minecraftArguments']).split():
                    if token not in legacy_tokens:
                        legacy_tokens.append(token)
            if data.get('mainClass'):
                merged['mainClass'] = data['mainClass']
            if data.get('jar'):
                merged['jar'] = data['jar']
        merged['id'] = version_id
        merged['libraries'] = list(libraries.values())
        if jvm_args or game_args:
            merged['arguments'] = {'jvm': jvm_args, 'game': game_args}
        if legacy_tokens:
            merged['minecraftArguments'] = ' '.join(legacy_tokens)
        if not merged.get('jar'):
            merged['jar'] = chain[-1][0]
        # classpath 上的客户端 jar 必须真实存在；
        # jar 指向缺失时沿继承链回退（老版 Forge 目录可能没有 jar）
        versions_dir = Path(mc_dir) / 'versions'
        jar_id = str(merged.get('jar') or '')
        if jar_id and not (versions_dir / jar_id / f'{jar_id}.jar').is_file():
            for dir_id, _data in reversed(chain):
                if (versions_dir / dir_id / f'{dir_id}.jar').is_file():
                    merged['jar'] = dir_id
                    break
        return merged

    def build_launch_command(self, version_id: str, prompt_java: bool=True, force_java_path: str='') -> tuple[list[str] | None, str]:
        if not version_id:
            return (None, '请先选择一个版本')
        mc_dir = self.config.get_mc_dir()
        actual_id, version_dir = self._resolve_version_id(version_id, mc_dir)
        if actual_id is None:
            return None, f'未找到有效的版本目录: {version_id}'
        final_id = actual_id
        version_settings = self.get_version_settings(version_id)
        version_java = str(version_settings.get('java_path', '') or '').strip()
        global_java = str(self.config.get('java_path', '') or '').strip()
        java_path = ''
        for candidate in version_java, global_java:
            if candidate and os.path.isfile(candidate):
                java_path = candidate
                break
        required = self._get_required_java_version(final_id)
        min_java, max_java, has_max = self._get_java_bounds(final_id, required)
        forced = str(force_java_path or '').strip()
        if forced and os.path.isfile(forced):
            java_path = forced
        elif java_path:
            configured_major = self._parse_java_major(self._get_java_version(java_path))
            if configured_major is not None and configured_major < min_java:
                logger.warning(f'版本 {final_id} 需要至少 Java {min_java}，但配置的 Java 是 {configured_major}，自动改用合适版本')
                java_path = ''
        if not java_path:
            best_ver, best_path = self._select_best_java(required, max_java if has_max else None)
            if best_path:
                java_path = best_path
                logger.info(f'版本 {final_id} 需要 Java {required}，已选择 Java {best_ver}')
            else:
                if prompt_java:
                    from PySide6.QtWidgets import QMessageBox
                    range_text = f'Java {min_java} ~ {max_java}' if has_max else f'Java {required} 或更高版本'
                    reply = QMessageBox.question(None, 'Java 未找到', f'Minecraft {final_id} 需要 {range_text}，但系统中未找到。\n是否打开官方下载页面？', QMessageBox.Yes | QMessageBox.No)
                    if reply == QMessageBox.Yes:
                        import webbrowser
                        webbrowser.open(f'https://adoptium.net/temurin/releases/?version={min_java}')
                if has_max:
                    return None, f'未找到合适的 Java（需要 JAVA{min_java} ~ JAVA{max_java}）'
                return None, f'未找到合适的 Java（需要 JAVA{required} 或更高版本）'
        if not java_path:
            return (None, '未检测到 Java，请在设置中配置 Java 路径')
        java_ok, java_info = self.validate_java(java_path)
        if not java_ok:
            return None, f'Java 不可用：{java_info}'
        login_mode = version_settings.get('login_mode', 'global')
        try:
            login_opts = self.auth.get_login_options(None if login_mode in ('', 'global') else login_mode)
        except Exception as exc:
            return None, str(exc)
        if login_opts.get('login_type') == 'offline' and not login_opts.get('username'):
            return (None, '请先在「账号设置」中填写离线昵称')
        mem_mode = str(version_settings.get('mem_mode') or 'global').lower()
        auto_memory = (
            mem_mode in ('', 'global')
            and str(self.config.get('mem_mode', 'auto')).lower() == 'auto'
        )
        if auto_memory:
            try:
                from core.memory import recommended_ram_mb
                from ui.version_meta import detect_installed_loader
                loader = detect_installed_loader(self.config.get_mc_dir(), final_id)
                auto_mb = recommended_ram_mb(self.config.get_mc_dir(), final_id, loader)
                mem_max = f'{max(512, int(auto_mb))}M'
                logger.info(f'PCL 动态内存分配: {mem_max}（版本 {final_id}，加载器 {loader or "原版"}）')
            except Exception as exc:
                logger.warning(f'动态内存分配失败，回退配置值: {exc}')
                auto_memory = False
                mem_max = self._format_mem(version_settings.get('memory_max') or self.config.get('memory_max', '4G'))
        else:
            mem_max = self._format_mem(version_settings.get('memory_max') or self.config.get('memory_max', '4G'))
        mem_min = self._format_mem(version_settings.get('memory_min') or self.config.get('memory_min', '512M'))
        def parse_mem_to_mb(mem_str):
            match = re.match('^(\\d+)([GMK])$', mem_str)
            if not match:
                return 2048
            val, unit = int(match.group(1)), match.group(2)
            if unit == 'G':
                return val * 1024
            if unit == 'M':
                return val
            if unit == 'K':
                return val / 1024
            return val

        max_mb = parse_mem_to_mb(mem_max)
        min_mb = parse_mem_to_mb(mem_min)
        if min_mb > max_mb:
            logger.warning(f'最小内存 {mem_min} > 最大内存 {mem_max}，自动调整')
            mem_min = mem_max
            min_mb = max_mb
            if not auto_memory:
                version_settings['memory_max'] = mem_max
                version_settings['memory_min'] = mem_min
                self.save_version_settings(version_id, version_settings)
        jvm_args = [f'-Xmx{mem_max}', f'-Xms{mem_min}']
        from core.jvm import (DEFAULT_JVM_ARGS, gc_arguments, split_arguments)
        def advanced(key, default=None):
            value = version_settings.get(key)
            if value is None or value == '':
                value = self.config.get(key, default)
            return value

        jvm_text = str(advanced('jvm_args', DEFAULT_JVM_ARGS) or '').strip()
        if jvm_text:
            jvm_args.extend(split_arguments(jvm_text))
        jvm_args.extend(gc_arguments(str(advanced('memory_gc', 'tuned_g1') or 'tuned_g1')))
        if bool(advanced('use_high_performance_gpu', True)):
            jvm_args.append('-Dorg.lwjgl.opengl.Display.allowSoftwareOpenGL=false')
        login_type = login_opts.get('login_type', 'offline')
        if login_type == 'thirdparty':
            try:
                agent = self.auth.ensure_authlib_injector()
                api = login_opts.get('authlib_api') or ''
                if not api:
                    return (None, '第三方账号缺少认证服务器地址，请重新登录')
                jvm_args.append(f'-javaagent:{agent}={api}')
                logger.info(f'已启用第三方登录: {api}')
            except Exception as exc:
                return None, f'第三方登录需要 authlib-injector：{exc}'
        elif login_type == 'offline':
            skin_settings = self.config.get('offline_skin', {}) or {}
            skin_mode = skin_settings.get('mode', 'random')
            if skin_mode not in ('', 'none', None):
                try:
                    from core.skin import SkinManager
                    skin = SkinManager().prepare_offline_skin(login_opts.get('username', ''), login_opts.get('uuid', ''))
                    if skin:
                        agent = self.auth.ensure_authlib_injector()
                        jvm_args.append(f'-javaagent:{agent}={skin["api"]}')
                        logger.info(f'已启用离线皮肤: {skin.get("skin")} ({skin.get("model")})')
                except Exception as exc:
                    logger.warning(f'离线皮肤启用失败，将使用默认皮肤: {exc}')
        raw_title_template = version_settings.get('window_title', '').strip()
        if not raw_title_template:
            raw_title_template = self.config.get('game_window_title', '').strip()
        static_ctx = {'{version}': final_id, '{username}': login_opts.get('username', 'Steve'), '{ram}': mem_max, '{launcher_name}': self.LAUNCHER_NAME, '{launcher_version}': self.LAUNCHER_VERSION}
        if raw_title_template:
            final_window_title = parse_placeholder(raw_title_template, static_ctx)
            jvm_args.append(f'-Dpcl.window.title={final_window_title}')
            logger.info(f'解析后自定义标题: {final_window_title}')
        else:
            final_window_title = ''
        self.last_window_title = final_window_title
        extra_args = []
        options = {**login_opts, 'jvmArguments': jvm_args, 'executablePath': java_path, 'launcherName': self.LAUNCHER_NAME, 'launcherVersion': self.LAUNCHER_VERSION, 'extraArguments': extra_args}
        try:
            from minecraft_launcher_lib.command import (get_arguments, get_arguments_string, get_libraries)
            merged = self._load_merged_version(final_id, mc_dir)
            options['nativesDirectory'] = str(Path(mc_dir) / 'versions' / final_id / 'natives')
            options['gameDirectory'] = str(mc_dir)
            classpath = get_libraries(merged, mc_dir)
            command = [java_path] + jvm_args
            arguments = merged.get('arguments') if isinstance(merged.get('arguments'), dict) else {}
            main_class = str(merged.get('mainClass') or '')
            if not main_class:
                return None, f'版本 {final_id} 缺少 mainClass'
            bootstrap = main_class == 'cpw.mods.bootstraplauncher.BootstrapLauncher'
            if bootstrap:
                game_jar_id = str(merged.get('jar') or '')
                if game_jar_id:
                    game_jar = Path(mc_dir) / 'versions' / game_jar_id / f'{game_jar_id}.jar'
                    game_jar_key = os.path.normcase(str(game_jar))
                    entries = [entry for entry in classpath.split(os.pathsep) if entry if os.path.normcase(entry) != game_jar_key]
                    classpath = os.pathsep.join(entries)
            if arguments.get('jvm'):
                jvm_from_json = get_arguments(arguments['jvm'], merged, mc_dir, options, classpath)
                if bootstrap:
                    cleaned = []
                    skip_classpath = False
                    for arg in jvm_from_json:
                        if skip_classpath:
                            skip_classpath = False
                            continue
                        if arg == '-cp':
                            skip_classpath = True
                            continue
                        cleaned.append(arg)
                    command.extend(cleaned)
                    command.append(f'-DlegacyClassPath={classpath}')
                else:
                    command.extend(jvm_from_json)
            else:
                command.append(f'-Djava.library.path={options["nativesDirectory"]}')
                if bootstrap:
                    command.append(f'-DlegacyClassPath={classpath}')
                else:
                    command.extend(['-cp', classpath])
            command.append(main_class)
            if merged.get('minecraftArguments'):
                command.extend(get_arguments_string(merged, mc_dir, options, classpath))
            else:
                command.extend(get_arguments(arguments.get('game') or [], merged, mc_dir, options, classpath))
            if '--lang' not in command:
                for i, arg in enumerate(command):
                    if arg == '--username' and i + 1 < len(command):
                        command.insert(i + 2, '--lang')
                        command.insert(i + 3, 'zh_cn')
                        break
            if self.is_version_isolated(final_id):
                self._migrate_legacy_isolated_dir(final_id)
                isolated_dir = Path(mc_dir) / 'versions' / final_id
                isolated_dir.mkdir(parents=True, exist_ok=True)
                found = False
                for i, arg in enumerate(command):
                    if arg == '--gameDir':
                        if i + 1 < len(command):
                            command[i + 1] = str(isolated_dir)
                        found = True
                        break
                if not found:
                    command.append('--gameDir')
                    command.append(str(isolated_dir))
            filtered_command = []
            skip_next = False
            for arg in command:
                if arg.startswith('--sun-misc-unsafe-memory-access='):
                    continue
                if arg == '--sun-misc-unsafe-memory-access':
                    skip_next = True
                    continue
                if skip_next:
                    skip_next = False
                    continue
                filtered_command.append(arg)
            command = filtered_command
            game_args = split_arguments(str(advanced('game_args', '') or ''))
            if game_args:
                command.extend(game_args)
            window_mode = str(self.config.get('game_window_size', 'default') or 'default')
            if window_mode != 'default':
                cleaned = []
                skip = False
                for arg in command:
                    if skip:
                        skip = False
                        continue
                    if arg in ('--width', '--height'):
                        skip = True
                        continue
                    if arg == '--fullscreen':
                        continue
                    cleaned.append(arg)
                command = cleaned
                if window_mode == 'fullscreen':
                    command.append('--fullscreen')
                else:
                    win_size = None
                    if window_mode == 'launcher':
                        win_size = int(self.config.get('launcher_window_width', 1152) or 1152), int(self.config.get('launcher_window_height', 648) or 648)
                    elif window_mode == 'custom':
                        win_size = int(self.config.get('game_window_width', 854) or 854), int(self.config.get('game_window_height', 480) or 480)
                    elif window_mode == 'maximized':
                        win_size = _screen_size()
                    if win_size:
                        command.extend(['--width', str(max(320, win_size[0])), '--height', str(max(240, win_size[1]))])
            auto_server = str(
                version_settings.get('auto_server')
                or login_opts.get('auto_server')
                or ''
            ).strip()
            if auto_server:
                host = auto_server
                port = ''
                if ':' in auto_server:
                    host, port = auto_server.rsplit(':', 1)
                command.extend(['--server', host])
                if port.isdigit():
                    command.extend(['--port', port])
            return command, ''
        except Exception as e:
            return None, f'构造启动命令失败：{str(e)}'

    @staticmethod
    def _bat_quote(arg: str) -> str:
        value = str(arg)
        if any((ch in value for ch in ' \t&|<>^"')):
            value = '"' + value.replace('"', '\\"') + '"'
        return value.replace('%', '%%')

    def build_launch_script(self, version_id: str) -> tuple[str, str]:
        '''生成可直接双击运行的 Windows 启动脚本（GBK 文本）。'''

        command, err = self.build_launch_command(version_id)
        if not command:
            return '', err
        command = [arg for arg in command if not arg.startswith('-javaagent:') if '127.0.0.1' not in arg]
        game_dir = self.get_game_dir(version_id)
        try:
            game_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        safe_title = version_id.replace('%', '%%')
        line = ' '.join((self._bat_quote(arg) for arg in command))
        content = f'chcp 936>nul\r\n@echo off\r\ntitle 启动 - {safe_title}\r\necho 游戏正在启动，请稍候。\r\ncd /D {self._bat_quote(str(game_dir))}\r\n\r\n{line}\r\necho 游戏已退出。\r\npause\r\n'
        return content, ''

    def pre_launch_check(self, version_id: str) -> tuple[bool, str]:
        mc_dir = self.config.get_mc_dir()
        ok, msg = self.config.validate_mc_dir(mc_dir)
        if not ok:
            return False, msg
        if not version_id:
            return (False, '请选择游戏版本')
        actual_id, version_dir = self._resolve_version_id(version_id, mc_dir)
        if actual_id is None:
            return False, f'版本 {version_id} 未安装'
        version_settings = self.get_version_settings(version_id)
        java_path = str(version_settings.get('java_path') or '').strip() or str(self.config.get('java_path') or '').strip()
        if not java_path:
            required = self._get_required_java_version(actual_id)
            _min_java, max_java, has_max = self._get_java_bounds(actual_id, required)
            best_ver, best_path = self._select_best_java(required, max_java if has_max else None)
            if not best_path:
                return False, f'未找到满足要求的 Java (需要 >= {required})'
            return (True, '')
        if not os.path.isfile(java_path):
            return (False, 'Java 路径无效')
        return (True, '')


def _screen_size() -> tuple:
    try:
        if sys.platform == 'win32':
            import ctypes
            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        return (1280, 720)
    except Exception:
        return (1280, 720)

def set_process_priority(pid: int, level: str) -> bool:
    '''设置游戏进程优先级（Windows）。'''

    if sys.platform != 'win32' or not pid:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_SET_INFORMATION = 512
        handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
        if not handle:
            return False
        classes = {'high': 128, 'normal': 32, 'low': 64}
        try:
            return bool(kernel32.SetPriorityClass(handle, classes.get(level, 32)))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False

def _game_window_handle(pid: int):
    """按进程查找可见游戏窗口；1.8.9 等低版本窗口可能暂时没有标题，因此也接受 LWJGL/GLFW 类名。"""

    if sys.platform != 'win32' or not pid:
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        best = {'hwnd': 0, 'area': 0}
        known_classes = ('lwjgl', 'glfw30', 'glfw', 'sunawtframe', 'minecraft')
        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value != pid:
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width < 200 or height < 120:
                return True
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            class_name = (class_buf.value or '').strip().lower()
            title_len = user32.GetWindowTextLengthW(hwnd)
            if class_name not in known_classes and title_len <= 0:
                return True
            area = width * height
            if area > best['area']:
                best['hwnd'] = hwnd
                best['area'] = area
            return True

        user32.EnumWindows(EnumProc(callback), 0)
        return int(best['hwnd'] or 0)
    except Exception:
        return 0


def _center_game_window(pid: int, move: bool=True):
    """查找并居中游戏窗口；兼容低版本无标题窗口。"""

    if sys.platform != 'win32' or not pid:
        return False, None
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = _game_window_handle(pid)
        if not hwnd:
            return False, None
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False, None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return False, None
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        if move:
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            user32.SetWindowPos(hwnd, 0, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER)
        return True, (x, y, width, height)
    except Exception:
        return False, None


def _set_window_title_for_pid(pid: int, title: str) -> bool:
    """Windows 下按进程 ID 修改游戏窗口标题；低版本窗口也适用。"""

    if sys.platform != 'win32' or not title:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = _game_window_handle(pid)
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        user32.SetWindowTextW(hwnd, str(title))
        return True
    except Exception:
        return False

class LaunchWorker(QThread):
    launch_success = Signal()
    window_ready = Signal()
    launch_error = Signal(str)
    process_exited = Signal(int)
    output_received = Signal(str)
    def __init__(self, command: list[str], mc_dir: str, pre_launch_command: str='', window_title: str=''):
        super().__init__()
        self.command = command
        self.mc_dir = mc_dir
        self.pre_launch_command = (pre_launch_command or '').strip()
        self.window_title = (window_title or '').strip()
        self._process = None
        self.version_id = None
        self._stop_flag = False
        self._stop_lock = threading.Lock()
        self._exited_emitted = False
        self._window_ready_emitted = False
        for i, arg in enumerate(command):
            if arg == '--version' and i + 1 < len(command):
                self.version_id = command[i + 1]
                break

    def _ensure_chinese_options(self):
        config = ConfigManager()
        if not config.get('auto_set_chinese', True):
            return
        if not self.version_id:
            logger.warning('无法获取版本 ID，跳过自动设置中文')
            return
        launch_mgr = LaunchManager()
        version_settings = launch_mgr.get_version_settings(self.version_id)
        game_dir = None
        for i, arg in enumerate(self.command):
            if arg == '--gameDir' and i + 1 < len(self.command):
                game_dir = self.command[i + 1]
                break
        if not game_dir:
            if LaunchManager().is_version_isolated(self.version_id):
                game_dir = str(Path(self.mc_dir) / 'versions' / self.version_id)
            else:
                game_dir = self.mc_dir
        options_path = Path(game_dir) / 'options.txt'
        marker_path = Path(game_dir) / '.scl_auto_chinese_once'
        try:
            options_path.parent.mkdir(parents=True, exist_ok=True)
            current_lang = ''
            lines = []
            if options_path.exists():
                lines = options_path.read_text(
                    encoding='utf-8', errors='replace'
                ).splitlines()
                for line in lines:
                    if line.split(':', 1)[0].strip() == 'lang':
                        current_lang = line.split(':', 1)[1].strip()
                        break
            if marker_path.exists() and current_lang == 'zh_CN':
                return
            found = False
            for index, line in enumerate(lines):
                if line.split(':', 1)[0].strip() == 'lang':
                    lines[index] = 'lang:zh_CN'
                    found = True
                    break
            if not found:
                lines.append('lang:zh_CN')
            options_path.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')
            marker_path.write_text('1', encoding='utf-8')
            logger.info(f'已设置/校正中文语言: {options_path}')
        except Exception as e:
            logger.warning(f'设置 options.txt 中文语言失败: {e}')
            return

    def stop_game(self):
        '''强制终止进程树，阻塞等待进程完全退出'''

        with self._stop_lock:
            if self._stop_flag:
                return
            self._stop_flag = True
        proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                pid = proc.pid
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
                    proc.wait(timeout=3)
                else:
                    proc.terminate()
                    proc.wait(timeout=3)
            except Exception as e:
                logger.error(f'终止进程等待异常: {e}')
        with self._stop_lock:
            if not self._exited_emitted:
                self._exited_emitted = True
                exit_code = proc.poll() if proc is not None else -1
                self.process_exited.emit(exit_code)
            return

    def _post_launch_window_loop(self, pid: int):
        '''游戏窗口出现后：按设置改标题，并把窗口居中（连续多次位置不变后停止）。'''

        if sys.platform != 'win32':
            return
        try:
            center_enabled = bool(ConfigManager().get('game_window_center', True))
        except Exception:
            center_enabled = True
        title_done = not self.window_title
        last_rect = None
        stable = 0
        for _ in range(120):
            if self._stop_flag:
                return
            if not title_done and _set_window_title_for_pid(pid, self.window_title):
                logger.info(f'已修改游戏窗口标题: {self.window_title}')
                title_done = True
            ok, rect = _center_game_window(pid, move=center_enabled)
            if not ok:
                time.sleep(0.5)
                continue
            if not self._window_ready_emitted:
                self._window_ready_emitted = True
                self.window_ready.emit()
            if not center_enabled:
                return
            if rect == last_rect:
                stable += 1
                if stable >= 4 and title_done:
                    return
            else:
                stable = 0
            last_rect = rect
            time.sleep(0.5)

    def run(self):
        self._ensure_chinese_options()
        if self.pre_launch_command:
            self.output_received.emit(f'[启动前命令] {self.pre_launch_command}')
            try:
                result = subprocess.run(self.pre_launch_command, shell=True, cwd=self.mc_dir, capture_output=True, text=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                output = (result.stdout or '') + (result.stderr or '')
                for line in output.splitlines():
                    if line.strip():
                        self.output_received.emit(line.rstrip())
                if result.returncode != 0:
                    err = f'启动前命令执行失败（退出码 {result.returncode}）'
                    self.launch_error.emit(err)
                    if not self._exited_emitted:
                        self._exited_emitted = True
                        self.process_exited.emit(-2)
                return
            except Exception as exc:
                self.launch_error.emit(f'启动前命令执行失败：{exc}')
                if not self._exited_emitted:
                    self._exited_emitted = True
                    self.process_exited.emit(-2)
                return
        proc = None
        try:
            try:
                startupinfo = None
                if sys.platform == 'win32':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
                proc = subprocess.Popen(self.command, cwd=self.mc_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, encoding='utf-8', errors='replace', startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                self._process = proc
                self.launch_success.emit()
                threading.Thread(target=self._post_launch_window_loop, args=(proc.pid,), daemon=True).start()
                if proc.stdout is not None:
                    try:
                        for raw_line in iter(proc.stdout.readline, ''):
                            if self._stop_flag:
                                break
                            line = raw_line.rstrip()
                            if line:
                                self.output_received.emit(line)
                    except (ValueError, OSError):
                        pass
                if not self._exited_emitted:
                    if proc is not None:
                        exit_code = proc.poll()
                        if exit_code is None:
                            exit_code = proc.wait()
                    else:
                        exit_code = -1
                    with self._stop_lock:
                        if not self._exited_emitted:
                            self._exited_emitted = True
                            self.process_exited.emit(exit_code)
            except FileNotFoundError:
                if not self._exited_emitted:
                    self._exited_emitted = True
                    self.process_exited.emit(-1)
            except PermissionError:
                if not self._exited_emitted:
                    self._exited_emitted = True
                    self.process_exited.emit(-1)
            except Exception as e:
                logger.error(f'游戏线程异常: {e}')
                if not self._exited_emitted:
                    self._exited_emitted = True
                    self.process_exited.emit(-1)
        finally:
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait()
                except:
                    pass
            self._process = None


# WARNING: Decompyle incomplete
