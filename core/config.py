'''
配置管理模块 - 负责启动器配置的持久化存储与读取
'''

import copy
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)


class ConfigManager:
    '''单例配置管理器（线程安全初始化）'''

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance

    def _init(self):
        '''初始化配置目录和默认配置'''

        self._loaded_changed = False
        home = Path.home()
        config_dir = None
        # 绿色版配置必须跟随启动器目录；SCL_CONFIG_DIR 仅用于开发/测试隔离。
        override = os.environ.get('SCL_CONFIG_DIR', '').strip()
        if override:
            config_dir = Path(override)
        else:
            try:
                from core.paths import scl_dir
                portable = scl_dir()
                probe = portable / '.config_write_test'
                probe.write_text('ok', encoding='utf-8')
                probe.unlink()
                config_dir = portable
            except Exception:
                config_dir = None
        if config_dir is None:
            config_dir = home / '.mc_launcher_py'
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # HOME/USERPROFILE 指向不存在的目录或不可写时，不能让启动器
            # 在导入配置阶段直接崩溃；退回当前目录，再退回临时目录。
            for fallback in (
                Path.cwd() / '.mc_launcher_py',
                Path(os.environ.get('TEMP') or os.environ.get('TMP') or '.')
                / 'SparkCraftLauncher',
            ):
                try:
                    fallback.mkdir(parents=True, exist_ok=True)
                    config_dir = fallback
                    break
                except OSError:
                    continue
        self.config_dir = config_dir
        self.config_file = self.config_dir / 'config.json'
        legacy_config = home / '.mc_launcher_py' / 'config.json'
        if (
            not override
            and not self.config_file.exists()
            and legacy_config.is_file()
            and legacy_config != self.config_file
        ):
            try:
                shutil.copy2(legacy_config, self.config_file)
            except OSError:
                pass
        self.DEFAULT_MIRRORS = [
            'https://launchermeta.mojang.com',
            'https://bmclapi2.bangbang93.com',
            'https://openbmclapi.bangbang93.com',
        ]
        self._prepare_portable_config(home, bool(override))
        self._default = {
            'mc_dir': str(home / '.minecraft'),
            'java_path': '',
            'memory_max': '4G',
            'memory_min': '512M',
            'mem_mode': 'auto',
            'download_threads': 64,
            'download_speed_limit': 0.0,
            'default_isolation': 'off',
            'game_window_title': '',
            'launcher_visibility': 'keep',
            'game_process_priority': 'normal',
            'game_window_size': 'default',
            'game_window_width': 854,
            'game_window_height': 480,
            'game_window_center': True,
            'selected_version': '',
            'player_name': '',
            'offline_name_set': False,
            'login_type': 'offline',
            'microsoft_account': None,
            'thirdparty_account': None,
            'saved_accounts': [],
            'mirror': 'official',
            'mirror_list': self.DEFAULT_MIRRORS.copy(),
            'file_download_source': 'auto',
            'version_list_source': 'auto',
            'curseforge_api_base': '',
            'curseforge_api_key': '',
            'microsoft_client_id': 'c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb',
            'microsoft_redirect_uri': 'https://login.microsoftonline.com/common/oauth2/nativeclient',
            'jvm_args': '-XX:-OmitStackTraceInFastThrow -Djdk.lang.Process.allowAmbiguousCommands=True -Dfml.ignoreInvalidMinecraftCertificates=True -Dfml.ignorePatchDiscrepancies=True',
            'game_args': '',
            'pre_launch_command': '',
            'memory_gc': 'auto',
            'disable_java_launch_wrapper': False,
            'disable_lwjgl_unsafe_agent': False,
            'use_high_performance_gpu': True,
            'verify_ssl': True,
            'disable_mod_update': False,
            'disable_file_verification': False,
            'background_enabled': True,
            'background_index': 0,
            'background_fit': 'auto',
            'background_loop_mode': 'all_sequential',
            'background_opacity': 80,
            'background_blur': 0,
            'background_overlay': True,
            'background_video_audio': False,
            'background_video_pause_on_background': True,
            'background_music_pause_on_background': True,
            'background_video_volume': 70,
            'background_ui_depth': 0,
            'background_music_volume': 60,
            'background_music_muted': False,
            'background_music_index': 0,
            'background_music_paused': False,
            'background_music_autoplay': True,
            'community_source_pref': 'official',
            'mod_filename_format': 'bracket_title',
            'mod_manage_style': 'title_translated',
            'theme': 'system',
            'theme_user_set': False,
            'auto_set_chinese': True,
            'global_window_title': 'Spark Craft Launcher',
            'global_title_color': '#88DDDD',
            'title_bar_mode': 'text',
            'global_title_image': '',
            'launch_custom_info': '',
            'memory_optimize_before_launch': False,
            'default_nav_page': 'home',
            'lidong_snow_enabled': True,
            'newyear_fireworks_enabled': True,
            'homepage_mode': 'preset',
            'homepage_preset': 'minecraft_news',
            'homepage_local_path': '',
            'version_settings': {},
        }
        self.config = self._load()
        if self._loaded_changed:
            try:
                self.save()
            except Exception:
                pass

    @staticmethod
    def _merge_config_dicts(primary, secondary):
        '''以旧 home 配置为主、SCL 旧配置为辅合并，避免升级时丢账号。'''

        primary = primary if isinstance(primary, dict) else {}
        secondary = secondary if isinstance(secondary, dict) else {}
        merged = copy.deepcopy(secondary)
        merged.update(copy.deepcopy(primary))
        for key in (
            'version_settings', 'thirdparty_server_settings',
            'thirdparty_saved_passwords', 'version_nicknames', 'offline_skin',
        ):
            if isinstance(primary.get(key), dict) and isinstance(secondary.get(key), dict):
                merged[key] = {**secondary[key], **primary[key]}
        accounts = []
        seen = set()
        for account in (
            list(primary.get('saved_accounts') or [])
            + list(secondary.get('saved_accounts') or [])
        ):
            if not isinstance(account, dict):
                continue
            key = (
                str(account.get('id') or ''),
                str(account.get('type') or ''),
                str(account.get('uuid') or ''),
                str(account.get('name') or ''),
            )
            if key in seen:
                continue
            seen.add(key)
            accounts.append(account)
        if accounts:
            merged['saved_accounts'] = accounts
        for key in ('microsoft_account', 'thirdparty_account'):
            if primary.get(key):
                merged[key] = copy.deepcopy(primary[key])
        return merged

    def _prepare_portable_config(self, home: Path, override: bool):
        self._preloaded_config = None
        if override:
            return
        legacy = home / '.mc_launcher_py' / 'config.json'
        try:
            scl_config = (
                json.loads(self.config_file.read_text(encoding='utf-8'))
                if self.config_file.is_file() else None
            )
        except (OSError, ValueError):
            scl_config = None
        if isinstance(scl_config, dict) and scl_config.get('portable_config_migrated'):
            return
        try:
            legacy_config = (
                json.loads(legacy.read_text(encoding='utf-8'))
                if legacy.is_file() else None
            )
        except (OSError, ValueError):
            legacy_config = None
        if isinstance(legacy_config, dict):
            merged = self._merge_config_dicts(legacy_config, scl_config)
        elif isinstance(scl_config, dict):
            merged = copy.deepcopy(scl_config)
        else:
            return
        merged['portable_config_migrated'] = True
        self._preloaded_config = merged
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.config_file.with_suffix('.tmp')
            tmp.write_text(
                json.dumps(merged, indent=2, ensure_ascii=False),
                encoding='utf-8',
            )
            tmp.replace(self.config_file)
        except OSError:
            pass

    def _backup_bad_config(self):
        try:
            stamp = time.strftime('%Y%m%d-%H%M%S')
            backup = self.config_file.with_name(
                f'config.broken-{stamp}.json'
            )
            index = 2
            while backup.exists():
                backup = self.config_file.with_name(
                    f'config.broken-{stamp}-{index}.json'
                )
                index += 1
            shutil.copy2(self.config_file, backup)
            logger.warning('配置结构异常，已备份到 %s', backup)
        except OSError:
            pass

    @staticmethod
    def _valid_memory_text(value):
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([GgMm])\s*', value)
        if not match:
            return None
        amount = float(match.group(1))
        if amount <= 0:
            return None
        unit = match.group(2).upper()
        mb = amount * 1024 if unit == 'G' else amount
        if mb < 512:
            return None
        return f'{amount:g}{unit}'

    @staticmethod
    def _sanitize_account(account):
        if not isinstance(account, dict):
            return None
        result = dict(account)
        for key in (
            'name', 'uuid', 'email', 'access_token', 'refresh_token',
            'avatar_url', 'skin_url', 'skin_variant', 'id', 'api',
            'server_name', 'register_url', 'auto_server', 'client_token',
            'type',
        ):
            value = result.get(key)
            if value is not None and not isinstance(value, str):
                result[key] = str(value)
        for key in ('expires_at', 'skin_updated_at', 'issued_at', 'last_checked_at'):
            value = result.get(key)
            if value is not None:
                try:
                    result[key] = float(value)
                except (TypeError, ValueError):
                    result.pop(key, None)
        return result

    def _sanitize_version_settings(self, value):
        if not isinstance(value, dict):
            return {}
        result = dict(value)
        for key in ('memory_max', 'memory_min'):
            if key in result:
                valid = self._valid_memory_text(result[key])
                if valid is None:
                    result.pop(key, None)
                else:
                    result[key] = valid
        for key in (
            'java_path', 'jvm_args', 'game_args', 'pre_launch_command',
            'game_window_title', 'selected_game_dir', 'memory_gc',
        ):
            if key in result and not isinstance(result[key], str):
                result.pop(key, None)
        for key in (
            'disable_file_verification', 'disable_mod_update',
            'disable_java_launch_wrapper', 'disable_lwjgl_unsafe_agent',
            'use_high_performance_gpu',
        ):
            if key in result and not isinstance(result[key], bool):
                if result[key] in (0, 1):
                    result[key] = bool(result[key])
                else:
                    result.pop(key, None)
        return result

    def _sanitize_config(self, raw: dict):
        '''把合法 JSON 但类型/结构错误的配置修正为可启动的默认值。'''

        changed = False
        hints = {
            'selected_game_dir': str,
            'offline_last_name': str,
            'last_microsoft_account_id': str,
            'launcher_window_width': int,
            'launcher_window_height': int,
            'game_dirs': list,
            'favorited_versions': list,
            'version_nicknames': dict,
            'thirdparty_server_settings': dict,
            'thirdparty_saved_passwords': dict,
            'thirdparty_saved_username': str,
            'thirdparty_saved_password': str,
            'thirdparty_remember': bool,
            'offline_skin': dict,
            'curseforge_api_key': str,
        }
        result = copy.deepcopy(raw)
        for key, value in list(raw.items()):
            if key not in self._default and key not in hints:
                # 迁移标记及未来新增键不能被 schema 清洗成 None。
                continue
            default = self._default.get(key, hints.get(key))
            if key in ('microsoft_account', 'thirdparty_account'):
                cleaned = self._sanitize_account(value)
                if cleaned != value:
                    result[key] = cleaned
                    changed = True
                continue
            if key in ('memory_max', 'memory_min'):
                valid = self._valid_memory_text(value)
                if valid is None:
                    result[key] = self._default[key]
                    changed = True
                else:
                    result[key] = valid
                continue
            if key == 'download_threads':
                if isinstance(value, bool):
                    value = self._default[key]
                elif isinstance(value, float) and value.is_integer():
                    value = int(value)
                elif isinstance(value, str):
                    try:
                        value = int(value.strip())
                    except (TypeError, ValueError):
                        value = self._default[key]
                if not isinstance(value, int):
                    value = self._default[key]
                value = max(1, min(256, value))
                result[key] = value
                changed = changed or value != raw[key]
                continue
            if key in ('game_window_width', 'game_window_height'):
                if isinstance(value, bool) or not isinstance(value, int):
                    result[key] = self._default[key]
                    changed = True
                elif value <= 0:
                    result[key] = self._default[key]
                    changed = True
                continue
            if key == 'login_type':
                if value not in ('offline', 'microsoft', 'thirdparty'):
                    result[key] = 'offline'
                    changed = True
                continue
            if key == 'theme':
                if value not in ('system', 'light', 'dark'):
                    result[key] = 'system'
                    changed = True
                continue
            if default is None:
                if value is not None:
                    result[key] = None
                    changed = True
                continue
            if isinstance(default, bool):
                if isinstance(value, bool):
                    continue
                if value in (0, 1):
                    result[key] = bool(value)
                else:
                    result[key] = default
                changed = True
                continue
            if isinstance(default, int):
                if isinstance(value, bool):
                    result[key] = default
                    changed = True
                    continue
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                elif isinstance(value, str):
                    try:
                        value = int(value.strip())
                    except (TypeError, ValueError):
                        value = default
                if not isinstance(value, int):
                    value = default
                result[key] = value
                changed = changed or value != raw[key]
                continue
            if isinstance(default, float):
                try:
                    result[key] = float(value)
                except (TypeError, ValueError):
                    result[key] = default
                changed = changed or result[key] != raw[key]
                continue
            if isinstance(default, str):
                if not isinstance(value, str):
                    result[key] = default
                    changed = True
                continue
            if isinstance(default, list):
                if not isinstance(value, list):
                    result[key] = copy.deepcopy(default)
                    changed = True
                elif key in ('mirror_list', 'favorited_versions'):
                    cleaned = [item for item in value if isinstance(item, str)]
                    if cleaned != value:
                        result[key] = cleaned
                        changed = True
                elif key == 'saved_accounts':
                    cleaned = []
                    for item in value:
                        account = self._sanitize_account(item)
                        if account is not None:
                            cleaned.append(account)
                    if cleaned != value:
                        result[key] = cleaned
                        changed = True
                continue
            if isinstance(default, dict):
                if not isinstance(value, dict):
                    result[key] = copy.deepcopy(default)
                    changed = True
                elif key == 'version_settings':
                    cleaned = {
                        str(vid): self._sanitize_version_settings(settings)
                        for vid, settings in value.items()
                        if isinstance(settings, dict)
                    }
                    if cleaned != value:
                        result[key] = cleaned
                        changed = True
                continue
        # 非默认键只接受提示类型，避免例如 selected_game_dir=123 之类在
        # 后续路径拼接处才爆炸。
        for key, kind in hints.items():
            if key not in result:
                continue
            value = result[key]
            if kind is str and not isinstance(value, str):
                result[key] = ''
                changed = True
            elif kind is int and (isinstance(value, bool) or not isinstance(value, int)):
                if key == 'launcher_window_width':
                    result[key] = 1280
                elif key == 'launcher_window_height':
                    result[key] = 800
                else:
                    result[key] = 0
                changed = True
            elif kind is list:
                if not isinstance(value, list):
                    result[key] = []
                    changed = True
                elif key in ('game_dirs', 'favorited_versions'):
                    cleaned = [item for item in value if isinstance(item, str)]
                    if cleaned != value:
                        result[key] = cleaned
                        changed = True
            elif kind is dict:
                if not isinstance(value, dict):
                    result[key] = {}
                    changed = True
                elif key == 'thirdparty_saved_passwords':
                    cleaned = {
                        str(user): str(password)
                        for user, password in value.items()
                        if isinstance(password, str)
                    }
                    if cleaned != value:
                        result[key] = cleaned
                        changed = True
                elif key == 'version_nicknames':
                    cleaned = {
                        str(version): str(nickname)
                        for version, nickname in value.items()
                    }
                    if cleaned != value:
                        result[key] = cleaned
                        changed = True
            elif kind is bool and not isinstance(value, bool):
                result[key] = bool(value) if value in (0, 1) else False
                changed = True
        offline_skin = result.get('offline_skin')
        if isinstance(offline_skin, dict):
            cleaned = dict(offline_skin)
            for field in ('mode', 'premium_name', 'model', 'custom_path', 'cape'):
                if field in cleaned and not isinstance(cleaned[field], str):
                    cleaned[field] = ''
                    changed = True
            if cleaned.get('mode') not in ('random', 'steve', 'alex', 'premium', 'custom'):
                cleaned['mode'] = 'random'
                changed = True
            if cleaned.get('model') not in ('auto', 'classic', 'slim'):
                cleaned['model'] = 'auto'
                changed = True
            result['offline_skin'] = cleaned
        return result, changed

    def _load(self) -> dict:
        user_config = getattr(self, '_preloaded_config', None)
        self._preloaded_config = None
        if user_config is not None:
            self._loaded_changed = True
        else:
            if not self.config_file.exists():
                return copy.deepcopy(self._default)
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._backup_bad_config()
                self._loaded_changed = True
                return copy.deepcopy(self._default)
            except OSError:
                return copy.deepcopy(self._default)
        if not isinstance(user_config, dict):
            self._backup_bad_config()
            self._loaded_changed = True
            return copy.deepcopy(self._default)
        sanitized, changed = self._sanitize_config(user_config)
        merged = {**self._default, **sanitized}
        if 'auto_set_chinese' not in sanitized:
            merged['auto_set_chinese'] = True
        if not sanitized.get('download_threads_max_migrated'):
            try:
                old_threads = int(sanitized.get('download_threads', 0) or 0)
            except (TypeError, ValueError):
                old_threads = 0
            # 恢复 PCL 的“最大线程数”语义：这里是上限，默认 64；
            # 实际并发仍由各下载任务按文件数量控制在较低值。
            if old_threads == 5:
                merged['download_threads'] = 64
            elif old_threads == 16 and sanitized.get('download_threads_migrated_16'):
                merged['download_threads'] = 64
            merged['download_threads_max_migrated'] = True
            changed = True
        if sanitized.get('global_window_title') == 'PMGL' and not sanitized.get('brand_renamed_scl'):
            merged['global_window_title'] = 'Spark Craft Launcher'
            merged['brand_renamed_scl'] = True
            changed = True
        if sanitized.get('global_title_color') == '#4a9eff' and not sanitized.get('brand_color_ffaa00'):
            merged['global_title_color'] = '#FFAA00'
            merged['brand_color_ffaa00'] = True
            changed = True
        if sanitized.get('global_title_color') in ('#FFAA00', '#4a9eff') and not sanitized.get('title_color_migrated_88dddd'):
            merged['global_title_color'] = '#88DDDD'
            merged['title_color_migrated_88dddd'] = True
            changed = True
        if not sanitized.get('theme_user_set'):
            merged['theme'] = 'system'
        # 旧版本默认 verify_ssl=False；这是不安全的默认值，首次升级时
        # 强制恢复校验。用户之后仍可在高级选项中显式关闭。
        if not sanitized.get('verify_ssl_secure_migrated'):
            merged['verify_ssl'] = True
            merged['verify_ssl_secure_migrated'] = True
            changed = True
        # 旧版本的默认值是 tuned_g1；迁移到自动 GC，并修复旧调优参数
        # 在超大堆下造成的周期性卡顿。用户仍可在高级选项中改回 G1。
        if not sanitized.get('memory_gc_auto_migrated'):
            if str(sanitized.get('memory_gc') or 'tuned_g1').lower() in ('', 'tuned_g1'):
                merged['memory_gc'] = 'auto'
            merged['memory_gc_auto_migrated'] = True
            changed = True
        self._loaded_changed = changed
        return merged

    def save(self):
        tmp_file = self.config_file.with_suffix('.tmp')
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            tmp_file.replace(self.config_file)
        except (OSError, TypeError, ValueError):
            try:
                if tmp_file.exists():
                    tmp_file.unlink()
            except OSError:
                pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def reset_to_defaults(self, preserve_accounts: bool = True,
                          preserve_paths: bool = True):
        '''恢复默认设置；默认保留账号凭据与游戏目录，避免误删用户数据。'''

        preserved = {}
        if preserve_accounts:
            for key in (
                'login_type', 'player_name', 'offline_name_set',
                'offline_last_name', 'microsoft_account',
                'thirdparty_account', 'saved_accounts',
                'thirdparty_remember', 'thirdparty_saved_username',
                'thirdparty_saved_password', 'thirdparty_saved_passwords',
                'last_microsoft_account_id', 'curseforge_api_base',
                'curseforge_api_key',
            ):
                if key in self.config:
                    preserved[key] = copy.deepcopy(self.config.get(key))
        if preserve_paths:
            for key in (
                'mc_dir', 'game_dirs', 'selected_game_dir',
                'launcher_window_width', 'launcher_window_height',
            ):
                if key in self.config:
                    preserved[key] = copy.deepcopy(self.config.get(key))
        self.config = copy.deepcopy(self._default)
        self.config.update(preserved)
        self.save()

    def set(self, key: str, value: Any, auto_save: bool = True):
        self.config[key] = value
        if auto_save:
            self.save()

    def get_mc_dir(self) -> str:
        value = self.get('mc_dir')
        return value if isinstance(value, str) and value else str(Path.home() / '.minecraft')

    def set_mc_dir(self, path: str):
        self.set('mc_dir', path)

    def _source_mirrors(self, key: str) -> List[str]:
        official = 'https://launchermeta.mojang.com'
        bmcl = 'https://bmclapi2.bangbang93.com'
        openbmcl = 'https://openbmclapi.bangbang93.com'
        source = str(self.get(key, 'auto') or 'auto').lower()
        if source == 'official':
            return [official]
        if source == 'bmclapi':
            return [bmcl, openbmcl, official]
        return [official, bmcl, openbmcl]

    def get_mirror_list(self) -> List[str]:
        '''文件下载源列表，供下载器按顺序尝试。'''

        return self._source_mirrors('file_download_source')

    def get_version_mirrors(self) -> List[str]:
        '''版本列表源列表。'''

        return self._source_mirrors('version_list_source')

    def get_mirror_url(self) -> str:
        '''当前首选的版本列表地址（用于 UI / 兼容旧代码）。'''

        mirrors = self.get_version_mirrors()
        if mirrors:
            return mirrors[0]
        return 'https://launchermeta.mojang.com'

    def validate_mc_dir(self, path: str) -> tuple[bool, str]:
        if not isinstance(path, str) or not path.strip():
            return False, '请输入游戏目录'
        path = path.strip()
        if sys.platform == 'win32':
            # 允许中文等 Unicode；只拒绝 Windows 路径真正不允许的字符。
            check = path[2:] if re.match(r'^[A-Za-z]:[\\/]', path) else path
            if re.search(r'[<>:"|?*\x00-\x1f]', check):
                return False, '路径包含 Windows 不允许的字符（<>:"|?*）'
            reserved = {'CON', 'PRN', 'AUX', 'NUL'}
            reserved.update(f'COM{i}' for i in range(1, 10))
            reserved.update(f'LPT{i}' for i in range(1, 10))
            for part in Path(path).parts:
                name = part.split('.', 1)[0].upper()
                if name in reserved:
                    return False, '路径包含 Windows 保留名称'
        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, '.write_test')
            with open(test_file, 'w', encoding='utf-8') as f:
                f.write('ok')
            os.remove(test_file)
            return True, ''
        except PermissionError:
            return False, '目录无写入权限，请更换路径或以管理员身份运行'
        except OSError as exc:
            return False, f'路径不可用：{str(exc)}'
