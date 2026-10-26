'''
账号认证 - 离线 / 微软正版 / 第三方 Authlib Injector(LittleSkin)
'''

import base64
import json
import logging
import os
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Optional
import requests
from PySide6.QtCore import QThread, Signal
try:
    from minecraft_launcher_lib.exceptions import AccountNotOwnMinecraft, InvalidRefreshToken
except ImportError:
    class AccountNotOwnMinecraft(Exception):
        pass

    class InvalidRefreshToken(Exception):
        pass

import minecraft_launcher_lib
from core.config import ConfigManager
from core.net import (HttpClient, is_network_error, network_available)
from core.paths import scl_dir
from core.secret import protect_secret, unprotect_secret
logger = logging.getLogger('Auth')
DEFAULT_LITTLESKIN_API = 'https://littleskin.cn/api/yggdrasil'
DEFAULT_LITTLESKIN_REGISTER = 'https://littleskin.cn/auth/register'
DEFAULT_MICROSOFT_CLIENT_ID = 'c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb'
MICROSOFT_REDIRECT_URI = 'https://login.microsoftonline.com/common/oauth2/nativeclient'
MICROSOFT_DEVICE_CODE_URL = 'https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode'
MICROSOFT_TOKEN_URL = 'https://login.microsoftonline.com/consumers/oauth2/v2.0/token'
MICROSOFT_SCOPE = 'XboxLive.signin offline_access'
AUTHLIB_INJECTOR_LATEST = 'https://authlib-injector.yushi.moe/artifact/latest.json'
AUTHLIB_INJECTOR_FALLBACK = 'https://authlib-injector.yushi.moe/artifact/56/authlib-injector-1.2.8.jar'
MINECRAFT_PROFILE_URL = 'https://api.minecraftservices.com/minecraft/profile'
MINECRAFT_ACTIVE_CAPE_URL = 'https://api.minecraftservices.com/minecraft/profile/capes/active'
CAPE_ALIAS_NAMES = {'Mojang-old': 'Mojang披风（旧版）', 'Mojang (Classic)': 'Mojang披风（旧版）', 'Mojang': 'Mojang披风', 'Mojang-new': 'Mojang Studios披风', 'Mojang Studios': 'Mojang Studios披风', 'Minecon2011': 'MINECON 2011参与者披风', 'MineCon 2011': 'MINECON 2011参与者披风', 'Minecon2012': 'MINECON 2012参与者披风', 'MineCon 2012': 'MINECON 2012参与者披风', 'Minecon2013': 'MINECON 2013参与者披风', 'MineCon 2013': 'MINECON 2013参与者披风', 'Minecon2015': 'MINECON 2015参与者披风', 'MineCon 2015': 'MINECON 2015参与者披风', 'Minecon2016': 'MINECON 2016参与者披风', 'MineCon 2016': 'MINECON 2016参与者披风', 'Translator': '翻译者披风', 'Translator-Chinese': '中文翻译者披风', 'Translator (Chinese)': '中文翻译者披风', 'Translator-Japanese': '日文翻译者披风', 'Translator (Japanese)': '日文翻译者披风', 'Mojira Moderator': 'Mojira管理员披风', 'Realms Mapmaker': 'Realms地图制作者披风', 'Cobalt': 'Cobalt锦标赛披风', 'Scrolls': 'Scrolls冠军披风', 'Millionth Customer': '百万购买者披风', 'Prismarine': '海晶石披风', 'Turtle': '海龟披风', 'Snowman': '雪人披风', 'Spade': '黑桃披风', 'dannyBstyle': 'dB披风', 'dB': 'dB披风', 'Birthday': '生日披风', 'Valentine': '情人节披风', 'Oxeye': '牛眼披风', 'Test': '测试披风', 'Document': '文档披风', 'Migrator': '迁移者披风', 'Vanilla': '原版披风', 'Pan': '薄烤饼披风', "Founder's": '创始人披风', '15th Anniversary': '15周年纪念披风', 'Cherry Blossom': '樱花披风', 'Common': '普通披风', "Follower's": '追随者披风', 'Purple Heart': '紫色心形披风', 'Menace': '入侵披风', 'Home': '家园披风', 'Yearn': '渴望披风', 'Copper': '铜披风', 'Zombie Horse': '僵尸马披风', 'Builder': '建造者披风', 'Twisted': '扭曲披风', 'Hero': '英雄披风', 'Aurora': '极光披风', 'Mojang Office': 'Mojang办公室披风', 'MCC 15th Year': 'MCC 15周年披风', 'Minecraft Experience': '村民救援披风', 'Moonlight Trail': '月光小径披风', 'Crafter': '工匠披风'}
CAPE_UUID_NAMES = {'8a6cc02cc86e43f1': '迁移者披风', '62db1a06cba0d59a': '原版披风', '86a126427a9cf1c9': '15周年纪念披风', '6851f20cfe35bd03': '薄烤饼披风', '3c1a1e7e50fce5f0': '紫色心形披风', 'e22327fef7324090': '樱花披风', '2a92e051c5df531a': '追随者披风', '6dfe9599aa000ffc': '入侵披风', 'd89779ee546fe4f0': '家园披风', '8724e22a0e7fee60': '渴望披风', '93fa88bcdcdc9a36': '铜披风', 'afc3ba389452cbb1': '极光披风', 'd40975b63f693972': '僵尸马披风', 'e99ac5f5259571a7': 'Mojang办公室披风', '71c819d9c37cb61c': '建造者披风', '764e20cd7392bc24': '创始人披风', '07973351ab910408': '村民救援披风', 'b169480d76031eb0': 'MCC 15周年披风', 'd84910543ee2842f': '扭曲披风', '0ac9c5df08969fc1': '英雄披风', '910674a2d3964724': '月光小径披风', 'ff3b90ad33a937ef': '工匠披风', '1981aad373fa9754': 'MINECON 2016参与者披风', '72ee2cfcefbfc081': 'MINECON 2015参与者披风', '0e4cc75a5f8a886d': 'MINECON 2013参与者披风', 'fd7c1a3d5fa7a9f1': 'MINECON 2012参与者披风', 'ebc798c3f7eca2a3': 'MINECON 2011参与者披风', '9349fa25c64ae935': 'Realms地图制作者披风', '11a3dcc4d826d0a1': 'Mojang披风', 'cb5dd34bee340182': 'Mojang Studios披风', 'c00df589ebea3ad6': 'Mojang披风（旧版）', '129a4675704fa3b8': '翻译者披风', '8dd71c1ee6ec0ae4': 'Mojira管理员披风', '298ae017a64261ad': '中文翻译者披风', '696b6cc29946b968': '日文翻译者披风', '116bacd62b233157': 'Cobalt锦标赛披风', 'd059f1a18b159eb6': 'Scrolls冠军披风', '8c05ef3c54870d04': '百万购买者披风', '123b1c8b4352f76d': '海晶石披风', '3d528060ab734868': '海龟披风', 'aab5a23c7495fc70': '雪人披风', '77421d9cf72e07e9': '黑桃披风', 'f249694c066c160c': 'dB披风', 'd3c7ac835b24eb29': '生日披风', 'b78918fa2b4d6189': '情人节披风', '88f1509813f4e324': '牛眼披风', '5e68fa78bd9df310': '测试披风', '7a939dc1a7ad4505': '文档披风'}

def cape_uuid_fragment(cape_id: str) -> str:
    '''返回 cape UUID 对应的已知片段；优先按前 16 位匹配。'''

    normalized = str(cape_id or '').replace('-', '').lower()
    prefix = normalized[:16]
    if prefix in CAPE_UUID_NAMES:
        return prefix
    for fragment in CAPE_UUID_NAMES:
        if fragment and fragment in normalized:
            return fragment
    return prefix

class AuthError(Exception):
    def __init__(self, message: str='', kind: str='general'):
        super().__init__(message)
        self.kind = kind


def classify_auth_error(exc: Exception) -> str:
    '''把登录异常归类为 no_game / expired / network / general。'''

    kind = str(getattr(exc, 'kind', '') or '')
    if kind in ('no_game', 'expired', 'network', 'general'):
        return kind
    if is_network_error(exc):
        return 'network'
    text = str(exc or '')
    if isinstance(exc, AccountNotOwnMinecraft):
        return 'no_game'
    if '未拥有 Minecraft' in text or '未拥有游戏' in text or 'AccountNotOwnMinecraft' in text:
        return 'no_game'
    if isinstance(exc, InvalidRefreshToken):
        return 'expired'
    lowered = text.lower()
    if 'invalid_grant' in lowered or 'refresh token' in lowered or '登录已失效' in text or '登录已过期' in text:
        return 'expired'
    return 'general'

def friendly_auth_error(message, kind: str='general') -> str:
    '''把底层异常转成普通用户能看懂的提示。'''

    text = str(message or '').strip()
    lowered = text.lower()
    if any((marker in lowered for marker in ('expecting value', 'expecting property name', 'jsondecodeerror', 'invalid json', 'char 0', 'unexpected end of json', 'not valid json'))):
        return '登录服务暂时返回了异常数据，请检查网络后稍等片刻再试。'
    if any((marker in lowered for marker in ('timed out', 'timeout', 'connection', 'ssl', 'getaddrinfo', 'name resolution', 'proxy', 'network', 'remote end closed', 'connection reset', 'eof occurred'))) or kind == 'network':
        return '网络连接不稳定，请检查网络或开启加速器后重试。'
    if 'invalid_grant' in lowered or kind == 'expired':
        return '登录状态已过期，请重新登录正版账号。'
    if kind == 'no_game':
        return '该正版账号未拥有 Minecraft，请确认账号后重试。'
    text = text.replace('微软登录失败：', '').replace('微软登录失败:', '')
    text = text.replace('Microsoft login failed:', '').strip()
    if not text or text.lower() in ('none', 'null'):
        return '登录失败，请稍后重试。'
    if len(text) > 200 or 'traceback' in lowered:
        return '登录失败，请稍后重试。'
    return text

def _is_transient_login_error(exc: Exception) -> bool:
    text = str(exc or '').lower()
    return any((marker in text for marker in ('expecting value', 'expecting property name', 'jsondecodeerror', 'char 0', 'unexpected end of json', 'not valid json', 'timed out', 'timeout', 'remote end closed', 'connection reset', 'connection aborted', 'temporarily unavailable', 'eof occurred', 'max retries exceeded', 'failed to establish')))

class AuthManager:
    def __init__(self):
        self.config = ConfigManager()
        self.last_error = ''

    @property
    def CLIENT_ID(self) -> str:
        value = os.environ.get('PMGL_MICROSOFT_CLIENT_ID', '').strip() or str(self.config.get('microsoft_client_id', '') or '').strip()
        if not value or value == '00000000402B5328':
            return DEFAULT_MICROSOFT_CLIENT_ID
        return value

    @property
    def REDIRECT_URI(self) -> str:
        return str(self.config.get('microsoft_redirect_uri', '') or '').strip() or MICROSOFT_REDIRECT_URI

    def get_oauth_login_info(self) -> dict:
        url, state, code_verifier = minecraft_launcher_lib.microsoft_account.get_secure_login_data(self.CLIENT_ID, self.REDIRECT_URI)
        return {'url': url, 'state': state, 'code_verifier': code_verifier}

    def complete_oauth_login(self, auth_code: str, code_verifier: str) -> dict:
        if not code_verifier:
            raise AuthError('登录会话已失效，请重新点击「打开登录页面」。')
        account = minecraft_launcher_lib.microsoft_account.complete_login(self.CLIENT_ID, None, self.REDIRECT_URI, auth_code, code_verifier)
        info = self._microsoft_account_info(account)
        self.config.set('login_type', 'microsoft')
        self.config.set('microsoft_account', info)
        self.config.save()
        self.save_microsoft_account(info)
        return info

    @staticmethod
    def _microsoft_account_info(account: dict) -> dict:
        name = account.get('name', 'Steve')
        return {'name': name, 'uuid': account.get('id') or account.get('uuid', ''), 'access_token': account.get('access_token', ''), 'refresh_token': account.get('refresh_token', ''), 'expires_at': time.time() + 3600, 'avatar_url': f'https://mc-heads.net/avatar/{name}/96'}

    def _refresh_microsoft_token(self, refresh_token: str) -> dict:
        last_exc = None
        for attempt in range(2):
            try:
                refreshed = minecraft_launcher_lib.microsoft_account.complete_refresh(self.CLIENT_ID, None, self.REDIRECT_URI, refresh_token)
                info = self._microsoft_account_info(refreshed)
                if not info.get('access_token'):
                    raise AuthError('登录服务没有返回访问令牌，请稍后重试。', 'general')
                return info
            except AccountNotOwnMinecraft as exc:
                raise AuthError('该正版账号未拥有 Minecraft。', 'no_game') from exc
            except InvalidRefreshToken as exc:
                raise AuthError('保存的正版账号登录状态已失效，需要重新登录。', 'expired') from exc
            except AuthError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt == 0 and _is_transient_login_error(exc):
                    time.sleep(0.8)
                    continue
                kind = classify_auth_error(exc)
                raise AuthError(friendly_auth_error(exc, kind), kind) from exc
        raise AuthError(friendly_auth_error(last_exc, classify_auth_error(last_exc)), 'general')

    def refresh_microsoft_login(self) -> Optional[dict]:
        account = self.config.get('microsoft_account') or {}
        refresh_token = account.get('refresh_token')
        if not refresh_token:
            return
        info = self._refresh_microsoft_token(refresh_token)
        self.config.set('microsoft_account', info)
        self.config.save()
        self.save_microsoft_account(info)
        return info

    def _minecraft_authorized_request(self, method: str, url: str, payload: Optional[dict]=None) -> requests.Response:
        '''请求 Minecraft 服务 API，遇到 401 自动刷新一次登录令牌。'''

        account = self.config.get('microsoft_account') or {}
        token = account.get('access_token', '')
        if not token:
            raise AuthError('请先登录微软正版账号')
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json', 'User-Agent': 'PMGL-Launcher/1.1'}
        last_exc = None
        for attempt in range(2):
            try:
                resp = requests.request(method, url, headers=headers, json=payload, timeout=30)
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    continue
                raise AuthError(f'连接 Minecraft 服务失败：{exc}') from exc
            if resp.status_code == 401 and attempt == 0:
                try:
                    refreshed = self.refresh_microsoft_login()
                except Exception:
                    refreshed = None
                if refreshed:
                    headers['Authorization'] = f'Bearer {refreshed.get("access_token", "")}'
                    continue
            return resp
        raise AuthError(f'连接 Minecraft 服务失败：{last_exc}')

    @staticmethod
    def _cape_error_message(resp: requests.Response) -> str:
        try:
            data = resp.json()
        except Exception:
            data = {}
        message = data.get('errorMessage') or data.get('error') or resp.text
        return str(message or f'HTTP {resp.status_code}')[:300]

    def get_minecraft_profile(self) -> dict:
        '''获取当前正版账号的官方资料（皮肤、披风、UUID）。'''

        resp = self._minecraft_authorized_request('get', MINECRAFT_PROFILE_URL)
        if resp.status_code == 401:
            raise AuthError('正版账号登录已过期，请重新登录。', 'expired')
        if resp.status_code != 200:
            raise AuthError(f'获取正版账号资料失败：{self._cape_error_message(resp)}', 'general')
        try:
            data = resp.json()
        except Exception as exc:
            raise AuthError('正版账号资料返回异常，请检查网络后稍后重试。', 'general') from exc
        if not isinstance(data, dict):
            raise AuthError('正版账号资料格式异常，请稍后重试。', 'general')
        skins = []
        for item in data.get('skins') or []:
            if not (isinstance(item, dict) and item.get('url')):
                continue
            variant = str(item.get('variant') or 'CLASSIC').lower()
            skins.append({'id': item.get('id', ''), 'state': str(item.get('state') or 'INACTIVE').upper(), 'url': item.get('url', ''), 'variant': 'slim' if variant == 'slim' else 'classic'})
        active_skin = next((item for item in skins if item.get('state') == 'ACTIVE'), skins[0] if skins else None)
        return {'id': data.get('id') or '', 'name': data.get('name') or '', 'skins': skins, 'active_skin': active_skin, 'capes': data.get('capes') or []}

    def get_active_minecraft_skin(self) -> dict:
        '''返回当前正版账号正在使用的官方皮肤信息。'''

        profile = self.get_minecraft_profile()
        skin = profile.get('active_skin')
        if not skin:
            raise AuthError('当前正版账号没有可用的皮肤。', 'general')
        return {'name': profile.get('name', ''), 'uuid': profile.get('id', ''), **skin}

    def fetch_thirdparty_profile(self) -> dict:
        '''读取第三方 Yggdrasil 账号的官方皮肤资料。'''

        account = self.config.get('thirdparty_account') or {}
        api = str(account.get('api') or '').rstrip('/')
        uid = str(account.get('uuid') or '').replace('-', '')
        if not api or not uid:
            raise AuthError('第三方账号信息不完整，请重新登录。', 'expired')
        url = f'{api}/sessionserver/session/minecraft/profile/{uid}'
        try:
            resp = requests.get(url, params={'unsigned': 'false'}, timeout=20, headers={'User-Agent': 'PMGL-Launcher/1.1'})
        except Exception as exc:
            kind = classify_auth_error(exc)
            raise AuthError(friendly_auth_error(exc, kind), kind) from exc
        if resp.status_code == 404:
            raise AuthError('第三方认证服务器找不到该账号资料。', 'general')
        if resp.status_code != 200:
            raise AuthError(f'获取第三方账号资料失败（HTTP {resp.status_code}）。', 'general')
        try:
            data = resp.json()
        except Exception as exc:
            raise AuthError('第三方账号资料返回异常，请稍后重试。', 'general') from exc
        textures = {}
        for prop in data.get('properties') or []:
            if prop.get('name') != 'textures' or not prop.get('value'):
                continue
            try:
                raw = base64.b64decode(str(prop['value']) + '===')
                payload = json.loads(raw.decode('utf-8'))
                textures = payload.get('textures') or {}
            except Exception:
                pass
        skin = textures.get('SKIN') or {}
        if not skin.get('url'):
            raise AuthError('该第三方账号没有设置皮肤。', 'general')
        metadata = skin.get('metadata') or {}
        return {'name': data.get('name') or account.get('name', ''), 'uuid': data.get('id') or uid, 'skin_url': skin.get('url', ''), 'model': 'slim' if str(metadata.get('model', '')).lower() == 'slim' else 'classic', 'cape_url': (textures.get('CAPE') or {}).get('url', '')}

    def get_minecraft_capes(self) -> list:
        '''从 Minecraft 官方账号接口获取当前账号拥有的披风列表。'''

        resp = self._minecraft_authorized_request('get', MINECRAFT_PROFILE_URL)
        if resp.status_code == 401:
            raise AuthError('正版账号登录已过期，请重新登录后再试')
        if resp.status_code != 200:
            raise AuthError(f'获取披风列表失败：{self._cape_error_message(resp)}')
        try:
            data = resp.json()
        except Exception as exc:
            raise AuthError(f'披风列表解析失败：{exc}') from exc
        capes = []
        alias_lookup = {str(key).lower(): value for key, value in CAPE_ALIAS_NAMES.items()}
        for item in data.get('capes') or []:
            cape_id = item.get('id') or ''
            if not cape_id:
                continue
            alias = str(item.get('alias') or '').strip()
            uuid_fragment = cape_uuid_fragment(cape_id)
            uuid_name = CAPE_UUID_NAMES.get(uuid_fragment)
            if not uuid_name:
                uuid_name = alias_lookup.get(alias.lower())
            name = uuid_name or alias or '未知披风'
            capes.append({'id': cape_id, 'alias': alias, 'english_name': alias, 'name': name, 'state': item.get('state', 'INACTIVE'), 'url': item.get('url', '')})
        uuid_order = {key: index for index, key in enumerate(CAPE_UUID_NAMES)}
        capes.sort(key=(lambda cape: (uuid_order.get(cape_uuid_fragment(cape.get('id', '')), len(uuid_order)), str(cape.get('name', '')))))
        return capes

    def set_minecraft_cape(self, cape_id: str='') -> bool:
        '''应用披风；传空字符串表示隐藏披风。'''

        if cape_id:
            resp = self._minecraft_authorized_request('put', MINECRAFT_ACTIVE_CAPE_URL, {'capeId': cape_id})
        else:
            resp = self._minecraft_authorized_request('delete', MINECRAFT_ACTIVE_CAPE_URL)
        if resp.status_code in (200, 204):
            return True
        if not cape_id:
            if resp.status_code == 404:
                return True
        if resp.status_code == 401:
            raise AuthError('正版账号登录已过期，请重新登录后再试')
        raise AuthError(f'切换披风失败：{self._cape_error_message(resp)}')

    def start_device_flow(self) -> dict:
        try:
            resp = requests.post(MICROSOFT_DEVICE_CODE_URL, data={'client_id': self.CLIENT_ID, 'scope': MICROSOFT_SCOPE}, headers={'User-Agent': 'PMGL-Launcher/1.1'}, timeout=20)
            data = resp.json()
        except Exception as exc:
            if is_network_error(exc) or not network_available():
                raise AuthError('因为网络问题导致验证登录失败！', 'network') from exc
            raise AuthError(f'获取微软设备码失败：{exc}') from exc
        if not isinstance(data, dict) or data.get('error'):
            desc = data.get('error_description') or data.get('error') or '未知错误'
            if not network_available():
                raise AuthError('因为网络问题导致验证登录失败！', 'network')
            raise AuthError(f'获取微软设备码失败：{desc}')
        return data

    def poll_device_flow(self, device_code: str, interval: int, expires_in: int, status_cb=None, cancel_cb=None) -> dict:
        import time as _time
        deadline = _time.time() + max(60, int(expires_in or 900))
        interval = max(2, int(interval or 5))
        last_error = ''
        def _sleep(seconds: float):
            end = _time.time() + seconds
            while _time.time() < end:
                if cancel_cb and cancel_cb():
                    raise AuthError('登录已取消')
                _time.sleep(min(0.25, max(0.0, end - _time.time())))

        try:
            resp = requests.post(MICROSOFT_TOKEN_URL, data={'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'client_id': self.CLIENT_ID, 'device_code': device_code}, headers={'User-Agent': 'PMGL-Launcher/1.1'}, timeout=20)
            data = resp.json()
        except Exception as exc:
            if is_network_error(exc) and not network_available():
                raise AuthError('因为网络问题导致验证登录失败！', 'network') from exc
            last_error = str(exc)
            _sleep(interval)
            # WARNING: continue outside loop (unrecovered structure)
        else:
            if isinstance(data, dict) and data.get('access_token'):
                return data
            error = (data or {}).get('error') if isinstance(data, dict) else ''
            if error == 'authorization_pending':
                _sleep(interval)
                # WARNING: continue outside loop (unrecovered structure)
            if error == 'slow_down':
                interval += 5
                _sleep(interval)
                # WARNING: continue outside loop (unrecovered structure)
            if error:
                desc = data.get('error_description') or error
                raise AuthError(f'微软登录失败：{desc}')
            _sleep(interval)
            while _time.time() < deadline:
                if cancel_cb and cancel_cb():
                    raise AuthError('登录已取消')
        if not network_available():
            raise AuthError('因为网络问题导致验证登录失败！', 'network')
        raise AuthError(f'微软登录超时，请重试。{last_error}')

    def complete_device_login(self, token_data: dict) -> dict:
        from minecraft_launcher_lib import microsoft_account as msa
        access_token = token_data.get('access_token', '')
        refresh_token = token_data.get('refresh_token', '')
        if not access_token:
            raise AuthError('微软登录未返回访问令牌')
        xbl = msa.authenticate_with_xbl(access_token)
        xbl_token = xbl.get('Token')
        if not xbl_token:
            raise AuthError('Xbox Live 认证失败，请确认账号状态')
        xsts = msa.authenticate_with_xsts(xbl_token)
        xsts_token = xsts.get('Token')
        userhash = ''
        try:
            userhash = xsts['DisplayClaims']['xui'][0]['uhs']
        except Exception:
            pass
        if not xsts_token or not userhash:
            raise AuthError('Xbox Live 安全令牌获取失败，请确认账号已创建 Xbox 档案')
        mc_auth = msa.authenticate_with_minecraft(userhash, xsts_token)
        mc_token = mc_auth.get('access_token')
        if not mc_token:
            raise AuthError('该正版账号未拥有 Minecraft，或未完成 Xbox 档案创建', 'no_game')
        profile = msa.get_profile(mc_token)
        if not isinstance(profile, dict) or profile.get('error'):
            if isinstance(profile, dict) and profile.get('error') == 'NOT_FOUND':
                raise AuthError('该正版账号未拥有 Minecraft', 'no_game')
            raise AuthError('获取 Minecraft 档案失败，请稍后重试', 'general')
        info = self._microsoft_account_info({'id': profile.get('id', ''), 'name': profile.get('name', 'Steve'), 'access_token': mc_token, 'refresh_token': refresh_token})
        self.config.set('login_type', 'microsoft')
        self.config.set('microsoft_account', info)
        self.config.save()
        self.save_microsoft_account(info)
        return info

    def get_offline_name(self) -> str:
        name = str(self.config.get('player_name', '') or '').strip()
        if not self.config.get('offline_name_set', False):
            if name == 'Steve':
                return ''
        return name

    def _offline_options(self) -> dict:
        player_name = self.get_offline_name()
        offline_uuid = ''
        if player_name:
            offline_uuid = uuid.uuid3(uuid.NAMESPACE_URL, f'OfflinePlayer:{player_name}').hex
        return {'username': player_name, 'uuid': offline_uuid, 'token': '0', 'access_token': '0', 'user_type': 'legacy', 'login_type': 'offline'}

    def get_thirdparty_account(self) -> dict:
        return self.config.get('thirdparty_account', {}) or {}

    def login_thirdparty(self, api: str, username: str, password: str, server_name: str='LittleSkin 登录', register_url: str=DEFAULT_LITTLESKIN_REGISTER, auto_server: str='') -> dict:
        api = (api or '').strip().rstrip('/')
        if not api.startswith(('http://', 'https://')):
            raise AuthError('认证服务器地址必须以 http:// 或 https:// 开头')
        if not username or not password:
            raise AuthError('请输入账号和密码')
        client_token = uuid.uuid4().hex
        payload = {'username': username, 'password': password, 'clientToken': client_token, 'agent': {'name': 'Minecraft', 'version': 1}, 'requestUser': False}
        try:
            resp = requests.post(f'{api}/authserver/authenticate', json=payload, timeout=20, headers={'User-Agent': 'PMGL-Launcher/1.1', 'Content-Type': 'application/json'})
            data = resp.json()
        except Exception as exc:
            if is_network_error(exc) or not network_available():
                raise AuthError('因为网络问题导致验证登录失败！', 'network') from exc
            raise AuthError(f'连接认证服务器失败：{exc}') from exc
        if not isinstance(data, dict) or data.get('error'):
            message = ''
            if isinstance(data, dict):
                message = data.get('errorMessage') or data.get('error') or ''
            raise AuthError(f'第三方登录失败：{message or "账号或密码错误"}')
        profile = data.get('selectedProfile')
        if not profile:
            profiles = data.get('availableProfiles') or []
            if profiles:
                profile = profiles[0]
        if not profile:
            raise AuthError('该账号还没有创建角色，请先到认证服务器网站创建角色。')
        account = {'api': api, 'access_token': data.get('accessToken', ''), 'client_token': data.get('clientToken', client_token), 'name': profile.get('name', username), 'uuid': profile.get('id', ''), 'email': username, 'avatar_url': f'https://littleskin.cn/avatar/{profile.get("id", "")}' if 'littleskin.cn' in api.lower() else f'https://mc-heads.net/avatar/{profile.get("name", username)}/96', 'server_name': server_name, 'register_url': register_url, 'auto_server': auto_server}
        self.config.set('login_type', 'thirdparty')
        self.config.set('thirdparty_account', account)
        self.config.set('microsoft_account', None)
        self.config.save()
        self.save_thirdparty_account(account)
        return account

    def logout(self):
        if self.config.get('login_type') == 'microsoft':
            account = self.config.get('microsoft_account') or {}
            if account.get('refresh_token') and account.get('name'):
                self.save_microsoft_account(account)
        self.config.set('login_type', 'offline')
        self.config.set('microsoft_account', None)
        self.config.set('thirdparty_account', None)
        self.config.save()

    def get_saved_accounts(self) -> list:
        value = self.config.get('saved_accounts', [])
        if isinstance(value, list):
            return value
        return []

    def _save_account_entry(self, entry: dict):
        accounts = self.get_saved_accounts()
        key = entry.get('id')
        for index, item in enumerate(accounts):
            if item.get('id') == key:
                accounts[index] = entry
                break
        else:
            accounts.append(entry)
        self.config.set('saved_accounts', accounts)

    @staticmethod
    def _thirdparty_credential_key(api: str, username: str) -> str:
        return f'{(api or "").strip().rstrip("/")}|{str(username or "").strip()}'

    def get_thirdparty_saved_password(self, api: str, username: str) -> str:
        credentials = self.config.get('thirdparty_saved_passwords', {}) or {}
        if not isinstance(credentials, dict):
            credentials = {}
        protected = credentials.get(self._thirdparty_credential_key(api, username))
        if not protected:
            if str(self.config.get('thirdparty_saved_username', '') or '') == str(username or ''):
                protected = self.config.get('thirdparty_saved_password', '') or ''
        if not protected:
            return ''
        try:
            return unprotect_secret(str(protected))
        except Exception:
            return ''

    def set_thirdparty_saved_password(self, api: str, username: str, password: str, remember: bool):
        credentials = self.config.get('thirdparty_saved_passwords', {}) or {}
        if not isinstance(credentials, dict):
            credentials = {}
        else:
            credentials = dict(credentials)
        key = self._thirdparty_credential_key(api, username)
        if remember and password:
            credentials[key] = protect_secret(password)
        else:
            credentials.pop(key, None)
        self.config.set('thirdparty_saved_passwords', credentials, auto_save=False)
        self.config.set('thirdparty_remember', bool(remember), auto_save=False)
        self.config.set('thirdparty_saved_username', username if remember else '', auto_save=False)
        self.config.set('thirdparty_saved_password', protect_secret(password) if remember and password else '', auto_save=False)
        self.config.save()

    def get_last_microsoft_account_entry(self) -> dict:
        '''获取最后使用的微软账号（即使已退出登录，saved_accounts 仍保留凭据）。'''

        accounts = [entry for entry in self.get_saved_accounts() if isinstance(entry, dict) if entry.get('type') == 'microsoft']
        if not accounts:
            return {}
        reusable = [entry for entry in accounts if entry.get('refresh_token')]
        pool = reusable or accounts
        last_id = str(self.config.get('last_microsoft_account_id', '') or '')
        if last_id:
            for entry in pool:
                if entry.get('id') == last_id:
                    return entry
        return pool[-1]

    def try_silent_microsoft_login(self) -> Optional[dict]:
        '''使用已保存的刷新令牌直接恢复微软登录，失败时抛出分类后的 AuthError。'''

        entry = self.get_last_microsoft_account_entry()
        refresh_token = str(entry.get('refresh_token') or '').strip()
        if not refresh_token:
            return
        info = self._refresh_microsoft_token(refresh_token)
        self.config.set('login_type', 'microsoft')
        self.config.set('microsoft_account', info)
        self.config.set('thirdparty_account', None)
        self.config.save()
        self.save_microsoft_account(info)
        return info

    def save_microsoft_account(self, info: dict):
        entry = {'id': f'microsoft:{info.get("uuid") or info.get("name")}', 'type': 'microsoft', 'name': info.get('name', ''), 'uuid': info.get('uuid', ''), 'email': info.get('email', ''), 'avatar_url': info.get('avatar_url', ''), 'access_token': info.get('access_token', ''), 'refresh_token': info.get('refresh_token', ''), 'expires_at': info.get('expires_at', 0), 'skin_url': info.get('skin_url', ''), 'skin_variant': info.get('skin_variant', ''), 'skin_updated_at': info.get('skin_updated_at', 0)}
        existing = next((item for item in self.get_saved_accounts() if item.get('id') == entry['id']), None)
        if existing:
            if not entry['refresh_token']:
                entry['refresh_token'] = existing.get('refresh_token', '')
            if not entry['email']:
                entry['email'] = existing.get('email', '')
            if not entry['avatar_url']:
                entry['avatar_url'] = existing.get('avatar_url', '')
            for key in ('skin_url', 'skin_variant', 'skin_updated_at'):
                if not entry.get(key):
                    entry[key] = existing.get(key, '' if key != 'skin_updated_at' else 0)
        self._save_account_entry(entry)
        self.config.set('last_microsoft_account_id', entry['id'], auto_save=False)
        self.config.save()

    def save_thirdparty_account(self, account: dict):
        entry = {'id': f'thirdparty:{account.get("api")}:{account.get("uuid")}', 'type': 'thirdparty', 'name': account.get('name', ''), 'uuid': account.get('uuid', ''), 'email': account.get('email', ''), 'avatar_url': account.get('avatar_url', ''), 'access_token': account.get('access_token', ''), 'client_token': account.get('client_token', ''), 'api': account.get('api', ''), 'server_name': account.get('server_name', ''), 'register_url': account.get('register_url', ''), 'auto_server': account.get('auto_server', '')}
        self._save_account_entry(entry)

    def save_offline_account(self, name: str):
        entry = {'id': f'offline:{name}', 'type': 'offline', 'name': name, 'uuid': ''}
        self._save_account_entry(entry)

    def upload_microsoft_skin(self, file_path: str, model: str='classic') -> bool:
        account = self.config.get('microsoft_account') or {}
        token = account.get('access_token')
        if not token:
            raise AuthError('正版账号登录已失效，请重新登录')
        with open(file_path, 'rb') as fh:
            content = fh.read()
        for attempt in range(2):
            try:
                resp = requests.post('https://api.minecraftservices.com/minecraft/profile/skins', headers={'Authorization': f'Bearer {token}', 'User-Agent': 'PMGL-Launcher/1.1'}, files={'file': ('skin.png', content, 'image/png')}, data={'variant': 'slim' if model == 'slim' else 'classic'}, timeout=30)
            except Exception as exc:
                raise AuthError(f'上传皮肤失败：{exc}') from exc
            if resp.status_code in (200, 204):
                return True
            if resp.status_code == 401 and attempt == 0:
                self.refresh_microsoft_login()
                account = self.config.get('microsoft_account') or {}
                token = account.get('access_token', token)
                continue
            try:
                message = resp.json().get('errorMessage') or resp.text
            except Exception:
                message = resp.text
            raise AuthError(f'上传皮肤失败：{message[:300]}')
        return False

    def validate_thirdparty_token(self, account: dict):
        '''校验第三方账号的 accessToken 是否仍然有效（切换账号前调用）。'''

        api = str((account or {}).get('api') or '').strip().rstrip('/')
        token = str((account or {}).get('access_token') or '').strip()
        client_token = str((account or {}).get('client_token') or '').strip()
        if not api.startswith(('http://', 'https://')) or not token:
            raise AuthError('第三方账号信息不完整，请重新输入密码登录。', 'expired')
        payload = {'accessToken': token, 'clientToken': client_token}
        try:
            resp = requests.post(
                f'{api}/authserver/validate', json=payload, timeout=15,
                headers={'User-Agent': 'PMGL-Launcher/1.1', 'Content-Type': 'application/json'},
            )
        except Exception as exc:
            if is_network_error(exc) or not network_available():
                raise AuthError('因为网络问题导致无法校验登录状态！', 'network') from exc
            raise AuthError(f'连接认证服务器失败：{exc}') from exc
        if resp.status_code == 403:
            raise AuthError('第三方登录状态已失效，请重新输入密码登录。', 'expired')
        if resp.status_code >= 500:
            raise AuthError(f'认证服务器暂时不可用（HTTP {resp.status_code}），请稍后重试。', 'general')
        return True

    def switch_to_account(self, entry: dict):
        kind = entry.get('type')
        if kind == 'microsoft':
            info = {'name': entry.get('name', ''), 'uuid': entry.get('uuid', ''), 'access_token': entry.get('access_token', ''), 'refresh_token': entry.get('refresh_token', ''), 'expires_at': entry.get('expires_at', 0), 'avatar_url': entry.get('avatar_url', '')}
            refresh_token = str(entry.get('refresh_token') or '').strip()
            if refresh_token:
                info = self._refresh_microsoft_token(refresh_token)
            self.config.set('login_type', 'microsoft')
            self.config.set('microsoft_account', info)
            self.config.set('thirdparty_account', None)
            self.config.set('last_microsoft_account_id', entry.get('id', ''), auto_save=False)
            self.config.save()
            self.save_microsoft_account(info)
            return info
        if kind == 'thirdparty':
            # 切换前校验令牌，失效时明确报错要求重新登录，
            # 避免“显示登录成功但实际无法启动（api=None）”的情况
            self.validate_thirdparty_token(entry)
            account = {'api': entry.get('api', ''), 'access_token': entry.get('access_token', ''), 'client_token': entry.get('client_token', ''), 'name': entry.get('name', ''), 'uuid': entry.get('uuid', ''), 'email': entry.get('email', ''), 'avatar_url': entry.get('avatar_url', ''), 'server_name': entry.get('server_name', ''), 'register_url': entry.get('register_url', ''), 'auto_server': entry.get('auto_server', '')}
            self.config.set('login_type', 'thirdparty')
            self.config.set('thirdparty_account', account)
            self.config.set('microsoft_account', None)
            self.config.save()
            self.save_thirdparty_account(account)
            return account
        if kind == 'offline':
            self.config.set('login_type', 'offline')
            self.config.set('player_name', entry.get('name', ''))
            self.config.set('offline_name_set', True)
            self.config.set('microsoft_account', None)
            self.config.set('thirdparty_account', None)
            self.config.save()
            return self._offline_options()
        raise AuthError('未知账号类型')

    def get_login_options(self, login_type: str=None) -> dict:
        login_type = login_type or self.config.get('login_type', 'offline')
        if login_type == 'microsoft':
            account = self.config.get('microsoft_account') or {}
            if not account.get('access_token'):
                raise AuthError('正版账号登录已失效，请重新登录。')
            expires_at = float(account.get('expires_at') or 0)
            if account.get('refresh_token') and (not expires_at or expires_at <= time.time() + 120):
                try:
                    refreshed = self.refresh_microsoft_login()
                    if refreshed:
                        account = refreshed
                except Exception as exc:
                    logger.warning(f'微软 Token 刷新失败: {exc}')
                    kind = classify_auth_error(exc)
                    if kind == 'network':
                        raise AuthError('因为网络问题导致验证登录失败！', 'network') from exc
                    if kind == 'expired':
                        raise AuthError('正版账号登录已过期，请到账号页重新登录。', 'expired') from exc
                    raise AuthError('正版账号登录失败，请稍后重试。', 'general') from exc
            return {'username': account.get('name', 'Steve'), 'uuid': account.get('uuid', ''), 'token': account.get('access_token', ''), 'access_token': account.get('access_token', ''), 'user_type': 'msa', 'login_type': 'microsoft'}
        if login_type == 'thirdparty':
            account = self.get_thirdparty_account()
            if not (account.get('access_token') and account.get('uuid')):
                raise AuthError('第三方账号登录已失效，请重新登录。')
            return {'username': account.get('name', 'Player'), 'uuid': account.get('uuid', ''), 'token': account.get('access_token', ''), 'access_token': account.get('access_token', ''), 'user_type': 'mojang', 'login_type': 'thirdparty', 'authlib_api': account.get('api', ''), 'auto_server': account.get('auto_server', '')}
        return self._offline_options()

    def ensure_authlib_injector(self) -> Path:
        cache = scl_dir() / 'authlib-injector.jar'
        if cache.exists() and cache.stat().st_size > 50000:
            return cache
        http = HttpClient(timeout=180, retries=4)
        download_url = AUTHLIB_INJECTOR_FALLBACK
        sha256 = ''
        try:
            meta = http.get_json(AUTHLIB_INJECTOR_LATEST)
            download_url = meta.get('download_url') or download_url
            sha256 = (meta.get('checksums') or {}).get('sha256') or ''
        except Exception:
            pass
        try:
            http.download(download_url, cache, sha256=sha256 or None, size=None)
        except Exception as exc:
            raise AuthError(f'下载 authlib-injector 失败：{exc}') from exc
        if not cache.exists() or cache.stat().st_size < 50000:
            raise AuthError('authlib-injector 下载不完整，请检查网络后重试。')
        return cache


class MicrosoftProfileWorker(QThread):
    '''后台获取正版账号官方资料（皮肤/披风），避免阻塞界面。'''

    success = Signal(dict)
    error = Signal(str, str)
    def run(self):
        try:
            self.success.emit(AuthManager().get_minecraft_profile())
        except AuthError as exc:
            self.error.emit(friendly_auth_error(exc, str(getattr(exc, 'kind', '') or 'general')), str(getattr(exc, 'kind', '') or 'general'))
            return
        except Exception as exc:
            kind = classify_auth_error(exc)
            self.error.emit(friendly_auth_error(exc, kind), kind)
            return


class SavedMicrosoftLoginWorker(QThread):
    '''使用已保存的刷新令牌登录指定正版账号。'''

    success = Signal(dict)
    error = Signal(str, str)
    def __init__(self, entry: dict):
        super().__init__()
        self.entry = dict(entry or {})

    def run(self):
        try:
            self.success.emit(AuthManager().switch_to_account(self.entry))
        except AuthError as exc:
            self.error.emit(friendly_auth_error(exc, str(getattr(exc, 'kind', '') or 'general')), str(getattr(exc, 'kind', '') or 'general'))
            return
        except Exception as exc:
            kind = classify_auth_error(exc)
            self.error.emit(friendly_auth_error(exc, kind), kind)
            return


class CapeListWorker(QThread):
    success = Signal(list)
    error = Signal(str)
    def run(self):
        try:
            self.success.emit(AuthManager().get_minecraft_capes())
        except Exception as exc:
            self.error.emit(str(exc))
            return


class CapeApplyWorker(QThread):
    success = Signal(str)
    error = Signal(str)
    def __init__(self, cape_id: str, cape_name: str=''):
        super().__init__()
        self.cape_id = cape_id
        self.cape_name = cape_name

    def run(self):
        try:
            AuthManager().set_minecraft_cape(self.cape_id)
            self.success.emit(self.cape_name or '无披风')
        except Exception as exc:
            self.error.emit(str(exc))
            return


class MicrosoftSilentLoginWorker(QThread):
    '''优先使用已保存的刷新令牌直接恢复微软登录。'''

    success = Signal(dict)
    error = Signal(str, str)
    def run(self):
        self.error.emit('没有已保存的正版账号', 'none')
        try:
            account = AuthManager().try_silent_microsoft_login()
            if account:
                self.success.emit(account)
            return
        except Exception as exc:
            kind = classify_auth_error(exc)
            self.error.emit(friendly_auth_error(exc, kind), kind)
            return


class MicrosoftDeviceLoginWorker(QThread):
    code_ready = Signal(str, str)
    status_changed = Signal(str)
    success = Signal(dict)
    error = Signal(str, str)
    def __init__(self):
        super().__init__()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            auth = AuthManager()
            self.status_changed.emit('正在获取微软设备码...')
            data = auth.start_device_flow()
            user_code = data.get('user_code', '')
            uri = data.get('verification_uri') or data.get('verification_url') or 'https://microsoft.com/link'
            interval = int(data.get('interval') or 5)
            expires_in = int(data.get('expires_in') or 900)
            if not user_code or not data.get('device_code'):
                raise AuthError('微软设备码响应不完整，请重试')
            self.code_ready.emit(user_code, uri)
            self.status_changed.emit(f'请在浏览器中输入代码：{user_code}')
            token_data = auth.poll_device_flow(data['device_code'], interval, expires_in, status_cb=self.status_changed.emit, cancel_cb=(lambda: self._cancelled))
            if self._cancelled:
                return
            self.status_changed.emit('正在验证 Xbox / Minecraft 账号...')
            account = auth.complete_device_login(token_data)
            if not self._cancelled:
                self.success.emit(account)
            return
        except Exception as exc:
            if not self._cancelled:
                kind = classify_auth_error(exc)
                self.error.emit(friendly_auth_error(exc, kind), kind)
                return
            return


class MicrosoftOAuthLoginWorker(QThread):
    success = Signal(dict)
    error = Signal(str)
    def __init__(self, auth_code, code_verifier):
        super().__init__()
        self.auth_code = auth_code
        self.code_verifier = code_verifier

    def run(self):
        try:
            auth = AuthManager()
            account = auth.complete_oauth_login(self.auth_code, self.code_verifier)
            self.success.emit(account)
        except Exception as e:
            self.error.emit(friendly_auth_error(e, classify_auth_error(e)))
            return


class ThirdPartyLoginWorker(QThread):
    success = Signal(dict)
    error = Signal(str, str)
    def __init__(self, api, username, password, server_name, register_url, auto_server):
        super().__init__()
        self.api = api
        self.username = username
        self.password = password
        self.server_name = server_name
        self.register_url = register_url
        self.auto_server = auto_server

    def run(self):
        try:
            auth = AuthManager()
            account = auth.login_thirdparty(self.api, self.username, self.password, self.server_name, self.register_url, self.auto_server)
            self.success.emit(account)
        except Exception as e:
            kind = classify_auth_error(e)
            self.error.emit(friendly_auth_error(e, kind), kind)
            return


# WARNING: Decompyle incomplete
