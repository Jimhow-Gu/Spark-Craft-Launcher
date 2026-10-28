'''
配置管理模块 - 负责启动器配置的持久化存储与读取
'''

import json
import os
import copy
import threading
from pathlib import Path
from typing import Any, List

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
                return cls._instance
        return cls._instance

    def _init(self):
        '''初始化配置目录和默认配置'''

        self.config_dir = Path.home() / '.mc_launcher_py'
        self.config_file = self.config_dir / 'config.json'
        self.config_dir.mkdir(exist_ok=True)
        self.DEFAULT_MIRRORS = ['https://launchermeta.mojang.com', 'https://bmclapi2.bangbang93.com', 'https://openbmclapi.bangbang93.com']
        self._default = {'mc_dir': str(Path.home() / '.minecraft'), 'java_path': '', 'memory_max': '4G', 'memory_min': '512M', 'mem_mode': 'auto', 'download_threads': 64, 'download_speed_limit': 0.0, 'default_isolation': 'off', 'game_window_title': '', 'launcher_visibility': 'keep', 'game_process_priority': 'normal', 'game_window_size': 'default', 'game_window_width': 854, 'game_window_height': 480, 'game_window_center': True, 'selected_version': '', 'player_name': '', 'offline_name_set': False, 'login_type': 'offline', 'microsoft_account': None, 'thirdparty_account': None, 'saved_accounts': [], 'mirror': 'official', 'mirror_list': self.DEFAULT_MIRRORS.copy(), 'file_download_source': 'auto', 'version_list_source': 'auto', 'curseforge_api_key': '', 'curseforge_api_base': '', 'microsoft_client_id': 'c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb', 'microsoft_redirect_uri': 'https://login.microsoftonline.com/common/oauth2/nativeclient', 'jvm_args': '-XX:-OmitStackTraceInFastThrow -Djdk.lang.Process.allowAmbiguousCommands=True -Dfml.ignoreInvalidMinecraftCertificates=True -Dfml.ignorePatchDiscrepancies=True', 'game_args': '', 'pre_launch_command': '', 'memory_gc': 'tuned_g1', 'disable_java_launch_wrapper': False, 'disable_lwjgl_unsafe_agent': False, 'use_high_performance_gpu': True, 'verify_ssl': False, 'disable_mod_update': False, 'disable_file_verification': False, 'background_enabled': True, 'background_index': 0, 'background_fit': 'auto', 'background_loop_mode': 'all_sequential', 'background_opacity': 80, 'background_blur': 0, 'background_overlay': True, 'background_video_audio': False, 'background_video_pause_on_background': True, 'background_music_pause_on_background': True, 'background_video_volume': 70, 'background_ui_depth': 0, 'background_music_volume': 60, 'background_music_muted': False, 'background_music_index': 0, 'background_music_paused': False, 'community_source_pref': 'official', 'mod_filename_format': 'bracket_title', 'mod_manage_style': 'title_translated', 'theme': 'system', 'theme_user_set': False, 'auto_set_chinese': True, 'global_window_title': 'Spark Craft Launcher', 'global_title_color': '#88DDDD', 'title_bar_mode': 'text', 'global_title_image': '', 'launch_custom_info': '', 'memory_optimize_before_launch': False, 'homepage_mode': 'preset', 'homepage_preset': 'minecraft_news', 'homepage_local_path': '', 'version_settings': {}}
        self.config = self._load()

    def _load(self) -> dict:
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                merged = {**self._default, **user_config}
                if 'auto_set_chinese' not in user_config:
                    merged['auto_set_chinese'] = True
                if user_config.get('download_threads') == 5 and not user_config.get('download_threads_migrated_64'):
                    merged['download_threads'] = 64
                    merged['download_threads_migrated_64'] = True
                if user_config.get('global_window_title') == 'PMGL' and not user_config.get('brand_renamed_scl'):
                    merged['global_window_title'] = 'Spark Craft Launcher'
                    merged['brand_renamed_scl'] = True
                if user_config.get('global_title_color') == '#4a9eff' and not user_config.get('brand_color_ffaa00'):
                    merged['global_title_color'] = '#FFAA00'
                    merged['brand_color_ffaa00'] = True
                if user_config.get('global_title_color') in ('#FFAA00', '#4a9eff') and not user_config.get('title_color_migrated_88dddd'):
                    merged['global_title_color'] = '#88DDDD'
                    merged['title_color_migrated_88dddd'] = True
                if not user_config.get('theme_user_set'):
                    merged['theme'] = 'system'
                return merged
            except (json.JSONDecodeError, IOError):
                return self._default.copy()
        return self._default.copy()

    def save(self):
        tmp_file = self.config_file.with_suffix('.tmp')
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            tmp_file.replace(self.config_file)
        except IOError:
            if tmp_file.exists():
                tmp_file.unlink()
                return

    def get(self, key: str, default: Any=None) -> Any:
        return self.config.get(key, default)

    def reset_to_defaults(self, preserve_accounts: bool=True, preserve_paths: bool=True):
        '''恢复默认设置；默认保留账号凭据与游戏目录，避免误删用户数据。'''

        preserved = {}
        if preserve_accounts:
            for key in ('login_type', 'player_name', 'offline_name_set', 'offline_last_name', 'microsoft_account', 'thirdparty_account', 'saved_accounts', 'thirdparty_remember', 'thirdparty_saved_username', 'thirdparty_saved_password', 'thirdparty_saved_passwords', 'last_microsoft_account_id', 'curseforge_api_key'):
                if key in self.config:
                    preserved[key] = copy.deepcopy(self.config.get(key))
        if preserve_paths:
            for key in ('mc_dir', 'game_dirs', 'selected_game_dir', 'launcher_window_width', 'launcher_window_height'):
                if key in self.config:
                    preserved[key] = copy.deepcopy(self.config.get(key))
        self.config = copy.deepcopy(self._default)
        self.config.update(preserved)
        self.save()

    def set(self, key: str, value: Any, auto_save: bool=True):
        self.config[key] = value
        if auto_save:
            self.save()

    def get_mc_dir(self) -> str:
        return self.get('mc_dir')

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
        import re
        if not re.match('^[\\x00-\\x7F]+$', path):
            return (False, '路径包含中文或特殊字符，请使用纯英文路径')
        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, '.write_test')
            with open(test_file, 'w') as f:
                f.write('ok')
            os.remove(test_file)
            return (True, '')
        except PermissionError:
            return (False, '目录无写入权限，请更换路径或以管理员身份运行')
        except OSError as e:
            return False, f'路径不可用：{str(e)}'


