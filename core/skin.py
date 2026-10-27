'''
离线皮肤支持
- 默认 Steve/Alex 随机
- 通过正版玩家名下载皮肤
- 本地皮肤文件
通过 authlib-injector + 本地轻量 Yggdrasil 皮肤 API 在游戏中生效。
'''

import base64
import json
import logging
import random
import re
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse
from core.config import ConfigManager
from core.net import HttpClient
from core.paths import scl_dir
logger = logging.getLogger('Skin')
SKIN_DIR_NAME = 'skins'
STEVE_URL = 'https://mc-heads.net/skin/MHF_Steve'
ALEX_URL = 'https://mc-heads.net/skin/MHF_Alex'
PREMIUM_URL = 'https://mc-heads.net/skin/{name}'
MINOTAR_SKIN_URL = 'https://minotar.net/skin/{name}'
BUNDLED_SKIN_DIR = Path(__file__).resolve().parent.parent / 'ui' / 'imgs'

def skin_cache_key(uuid: str='', name: str='') -> str:
    key = str(uuid or name or 'player').strip().replace('-', '')
    return re.sub('[^0-9A-Za-z_\\-]', '_', key)[:64] or 'player'

def microsoft_skin_cache_path(uuid: str='', name: str='') -> Path:
    return scl_dir() / SKIN_DIR_NAME / f'microsoft_{skin_cache_key(uuid, name)}.png'

def thirdparty_skin_cache_path(uuid: str='', name: str='') -> Path:
    return scl_dir() / SKIN_DIR_NAME / f'thirdparty_{skin_cache_key(uuid, name)}.png'

def detect_skin_model(path: str | Path) -> str:
    '''根据皮肤 PNG 自动判断 classic / slim。

    64x64 纤细皮肤右臂背面只占用到 x=52，x=53..55 为透明空白；
    经典皮肤该区域属于手臂背面，通常是不透明的。
    '''

    try:
        from PySide6.QtGui import QImage
        image = QImage(str(path))
    except Exception:
        pass
    else:
        if image.isNull() or image.width() < 64 or image.height() < 64:
            return 'classic'
        scale = image.width() / 64.0
        samples = []
        for x in (54, 55):
            for y in range(20, 32, 2):
                px = min(image.width() - 1, int(x * scale))
                py = min(image.height() - 1, int(y * scale))
                samples.append(image.pixelColor(px, py).alpha())
        if not samples:
            return 'classic'
        transparent = sum((1 for alpha in samples if alpha == 0))
        if transparent >= len(samples) * 0.8:
            return 'slim'
        return 'classic'

def skin_size_error(path: str | Path) -> str:
    '''校验皮肤尺寸，返回错误提示；空字符串表示可用。'''

    try:
        from PySide6.QtGui import QImage
        image = QImage(str(path))
    except Exception:
        return '无法读取皮肤文件，请选择有效的 PNG 图片。'
    if image.isNull():
        return '无法读取皮肤文件，请选择有效的 PNG 图片。'
    width, height = image.width(), image.height()
    if width < 64 or height < 32 or width % 64 != 0:
        return '皮肤尺寸不正确，请使用 64x64（或 64x32）的 PNG 皮肤。'
    if height not in (32, 64, width // 2, width):
        return '皮肤尺寸不正确，请使用 64x64（或 64x32）的 PNG 皮肤。'
    return ''

CAPE_PALETTE = {'Moonlight Trail': ('#12233f', '#3e64a8', '#c7dcff'), '樱花披风': ('#5c2438', '#c96a8c', '#ffe1ec'), '入侵披风': ('#102a17', '#2f7d3f', '#9dff9d'), '紫色心形披风': ('#2b1038', '#7137a8', '#f0b6ff'), '家园披风': ('#3b2413', '#a56a32', '#ffe0ab'), '薄煎饼披风': ('#6b4314', '#d9a441', '#fff1c4'), '村民救援披风': ('#1c2a38', '#50708c', '#c4e2ff'), '建造者披风': ('#3c2a10', '#9c7736', '#ffe08a'), '15 周年纪念披风': ('#1b1b1b', '#555555', '#ffcf3f'), 'Hero': ('#3a1414', '#a62b2b', '#ffd36b'), '极光披风': ('#0c2430', '#1f8a8a', '#9ffff1'), '铜披风': ('#4a2410', '#b8642c', '#ffd2a1'), '僵尸马披风': ('#223016', '#5d7a33', '#d5ff8f'), 'Mojang 办公室披风': ('#2c1f0e', '#8c6b2f', '#f4d58a'), '渴望披风': ('#2b0d2b', '#8f2d8f', '#ffb3ff'), '普通披风': ('#333333', '#8a8a8a', '#e8e8e8'), 'MCC 15 周年披风': ('#1a0f2e', '#6241a8', '#d8c7ff'), 'Twisted': ('#12302c', '#2f7d70', '#a9fff1'), '追随者披风': ('#1f1f1f', '#6b6b6b', '#cfcfcf')}


def avatar_urls_for_account(account: dict) -> list:
    """正版/第三方账号头像候选地址：先轻量镜像，官方纹理放最后。"""

    account = dict(account or {})
    name = str(account.get('name') or '').strip()
    uuid = str(account.get('uuid') or '').replace('-', '').strip()
    urls = []

    def add(url):
        url = str(url or '').strip()
        if url and url not in urls:
            urls.append(url)

    add(account.get('avatar_url'))
    if uuid:
        add(f'https://minotar.net/helm/{uuid}/96.png')
        add(f'https://mineskin.eu/avatar/{uuid}/96')
        add(f'https://crafthead.net/helm/{uuid}/96')
        add(f'https://mc-heads.net/avatar/{uuid}/96')
        add(f'https://crafatar.com/avatars/{uuid}?size=96&overlay')
        add(f'https://visage.surgeplay.com/face/96/{uuid}')
    if name:
        add(f'https://minotar.net/helm/{name}/96.png')
        add(f'https://mineskin.eu/helm/{name}/96.png')
        add(f'https://crafthead.net/helm/{name}/96')
        add(f'https://mc-heads.net/avatar/{name}/96')
    # 只把官方纹理当兜底（本地会裁剪成头部）；第三方皮肤 URL 直接显示会是整张皮肤图
    skin_url = str(account.get('skin_url') or '')
    if 'textures.minecraft.net' in skin_url:
        add(skin_url)
    return urls


class _SkinHandler(BaseHTTPRequestHandler):
    server_version = 'PMGL-Skin/1.0'
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, data: dict, status: int=200):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        manager = self.server.skin_manager
        path = urlparse(self.path).path
        if path in ('/', '/api/yggdrasil', '/api/yggdrasil/'):
            self._send_json({'meta': {'serverName': 'SCL Local Skin', 'implementationName': 'Spark Craft Launcher', 'implementationVersion': '1.1'}, 'skinDomains': ['127.0.0.1', 'localhost']})
            return
        if '/sessionserver/session/minecraft/profile/' in path or '/minecraft/profile/' in path:
            uid = path.rstrip('/').split('/')[-1]
            profile = manager.get_profile(uid)
            if profile is None:
                self._send_json({'error': 'ProfileNotFound', 'errorMessage': 'Profile not found'}, 404)
                return
            self._send_json(profile)
            return
        if path.startswith('/cape/'):
            uid = path[len('/cape/'):]
            if uid.endswith('.png'):
                uid = uid[:-4]
            data = manager.get_cape_bytes(uid)
            if data is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith('/skin/'):
            uid = path[len('/skin/'):]
            if uid.endswith('.png'):
                uid = uid[:-4]
            data = manager.get_skin_bytes(uid)
            if data is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith('/api/profiles/minecraft/'):
            name = unquote(path.rsplit('/', 1)[-1])
            profile = manager.find_by_name(name)
            if profile:
                self._send_json({'id': profile['id'], 'name': profile['name']})
                return
            self._send_json({'error': 'ProfileNotFound'}, 404)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        manager = self.server.skin_manager
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            try:
                self.rfile.read(length)
            except Exception:
                pass
        if '/sessionserver/session/minecraft/join' in path:
            self.send_response(204)
            self.end_headers()
            return
        if '/authserver/authenticate' in path:
            self._send_json({'error': 'ForbiddenOperationException', 'errorMessage': 'Offline mode'}, 403)
            return
        self.send_response(404)
        self.end_headers()


class LocalSkinServer:
    _instance: Optional['LocalSkinServer'] = None
    def __init__(self):
        self._skins = {}
        self._names = {}
        self._server = None
        self._thread = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = LocalSkinServer()
        return cls._instance

    @staticmethod
    def normalize_uuid(uid: str) -> str:
        return str(uid or '').replace('-', '').lower()

    def register(self, uid: str, name: str, skin_path: Path, model: str, cape_path: Optional[Path]=None):
        uid = self.normalize_uuid(uid)
        if not uid:
            return
        self._skins[uid] = {'id': uid, 'name': name, 'skin_path': Path(skin_path), 'model': 'slim' if (model or '').lower() == 'slim' else 'classic', 'cape_path': Path(cape_path) if cape_path else None}
        self._names[name.lower()] = uid

    def get_profile(self, uid: str) -> Optional[dict]:
        uid = self.normalize_uuid(uid)
        item = self._skins.get(uid)
        if not item:
            return
        base = self.base_url()
        skin_url = f'{base}/skin/{uid}.png'
        textures = {'timestamp': int(time.time() * 1000), 'profileId': uid, 'profileName': item['name'], 'textures': {'SKIN': {'url': skin_url, 'metadata': {'model': item['model']}}}}
        if item.get('cape_path'):
            textures['textures']['CAPE'] = {'url': f'{base}/cape/{uid}.png'}
        value = base64.b64encode(json.dumps(textures, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).decode('ascii')
        return {'id': uid, 'name': item['name'], 'properties': [{'name': 'textures', 'value': value}]}

    def find_by_name(self, name: str) -> Optional[dict]:
        uid = self._names.get(name.lower())
        if uid:
            return self.get_profile(uid)

    def get_skin_bytes(self, uid: str) -> Optional[bytes]:
        item = self._skins.get(self.normalize_uuid(uid))
        if not item:
            return
        try:
            return Path(item['skin_path']).read_bytes()
        except OSError:
            pass

    def get_cape_bytes(self, uid: str) -> Optional[bytes]:
        item = self._skins.get(self.normalize_uuid(uid))
        if not item or not item.get('cape_path'):
            return
        try:
            return Path(item['cape_path']).read_bytes()
        except OSError:
            pass

    def ensure_started(self) -> str:
        if self._server is not None:
            return self.base_url()
        server = ThreadingHTTPServer(('127.0.0.1', 0), _SkinHandler)
        server.daemon_threads = True
        server.skin_manager = self
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url()

    def base_url(self) -> str:
        if self._server is None:
            return ''
        return f'http://127.0.0.1:{self._server.server_address[1]}'

    def stop(self):
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
            self._thread = None


class SkinManager:
    def __init__(self):
        self.config = ConfigManager()
        self.dir = scl_dir() / SKIN_DIR_NAME
        self.dir.mkdir(parents=True, exist_ok=True)

    def get_settings(self) -> dict:
        value = self.config.get('offline_skin', {}) or {}
        return {'mode': value.get('mode', 'random'), 'premium_name': value.get('premium_name', ''), 'model': value.get('model', 'auto'), 'custom_path': value.get('custom_path', ''), 'cape': value.get('cape', '无披风') or '无披风'}

    def create_cape_texture(self, name: str) -> Optional[Path]:
        '''根据披风名称生成一张本地披风纹理（64x32）。'''

        name = (name or '').strip()
        if not name or name == '无披风':
            return
        slug = re.sub('[^0-9A-Za-z\\u4e00-\\u9fff]+', '_', name).strip('_')[:40] or 'cape'
        cape_dir = self.dir / 'capes'
        cape_dir.mkdir(parents=True, exist_ok=True)
        dest = cape_dir / f'{slug}.png'
        if dest.exists() and dest.stat().st_size > 64:
            return dest
        top, bottom, accent = CAPE_PALETTE.get(name, ('#24324f', '#5b76a8', '#dbe7ff'))
        try:
            from PySide6.QtCore import (QPoint, QRect, Qt)
            from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter)
            image = QImage(64, 32, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            gradient = QLinearGradient(0, 0, 0, 18)
            gradient.setColorAt(0.0, QColor(top))
            gradient.setColorAt(1.0, QColor(bottom))
            for rect in QRect(1, 1, 10, 16), QRect(12, 1, 10, 16):
                painter.fillRect(rect, gradient)
            painter.fillRect(QRect(1, 0, 10, 1), QColor(top))
            painter.fillRect(QRect(12, 0, 10, 1), QColor(top))
            painter.fillRect(QRect(0, 1, 1, 16), QColor(top))
            painter.fillRect(QRect(11, 1, 1, 16), QColor(top))
            painter.fillRect(QRect(22, 1, 1, 16), QColor(top))
            painter.fillRect(QRect(1, 17, 10, 1), QColor(bottom))
            painter.fillRect(QRect(12, 17, 10, 1), QColor(bottom))
            accent_color = QColor(accent)
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent_color)
            for cx in (6, 17):
                painter.drawPolygon([QPoint(cx, 5), QPoint(cx + 3, 9), QPoint(cx, 13), QPoint(cx - 3, 9)])
            painter.fillRect(QRect(1, 14, 10, 2), accent_color)
            painter.fillRect(QRect(12, 14, 10, 2), accent_color)
            painter.end()
            if not image.save(str(dest), 'PNG'):
                return
            return dest
        except Exception as exc:
            logger.warning(f'生成披风纹理失败: {exc}')
            return

    def save_settings(self, settings: dict):
        self.config.set('offline_skin', settings)

    @staticmethod
    def _resolve_custom_path(path_str: str) -> Path:
        path = Path(path_str or '')
        if not path_str:
            return path
        if path.is_absolute():
            return path
        roots = [Path.cwd(), Path(__file__).resolve().parent.parent, Path(sys.executable).resolve().parent]
        for root in roots:
            candidate = root / path
            if candidate.is_file():
                return candidate
        return path

    def _download_skin(self, url: str, filename: str) -> Optional[Path]:
        return self._download_skin_candidates([url], filename)

    def _download_skin_candidates(self, urls: list, filename: str) -> Optional[Path]:
        dest = self.dir / filename
        if dest.exists() and dest.stat().st_size > 64:
            return dest
        for url in urls:
            if not url:
                continue
            try:
                HttpClient(timeout=30).download(url, dest)
                if dest.exists() and dest.stat().st_size > 64:
                    return dest
            except Exception as exc:
                logger.warning(f'下载皮肤失败 {url}: {exc}')
                continue

    def _fallback_skin(self, filename: str, color: str) -> Path:
        dest = self.dir / filename
        if dest.exists() and dest.stat().st_size > 64:
            return dest
        try:
            from PySide6.QtGui import QColor, QImage
            image = QImage(64, 64, QImage.Format_ARGB32)
            image.fill(QColor(color))
            image.save(str(dest), 'PNG')
            return dest
        except Exception:
            dest.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=='))
            return dest

    def _default_skin(self, model: str) -> Path:
        is_alex = str(model).lower() == 'slim'
        filename = 'alex.png' if is_alex else 'steve.png'
        bundled = BUNDLED_SKIN_DIR / ('alex_skin.png' if is_alex else 'steve_skin.png')
        cached = self.dir / filename
        if bundled.is_file():
            try:
                shutil.copyfile(bundled, cached)
            except OSError:
                pass
            if cached.is_file() and cached.stat().st_size > 64:
                return cached
        urls = [ALEX_URL, 'https://minotar.net/skin/MHF_Alex'] if is_alex else [STEVE_URL, 'https://minotar.net/skin/MHF_Steve']
        path = self._download_skin_candidates(urls, filename)
        return path or self._fallback_skin(filename, '#c58b67' if is_alex else '#5b8fbe')

    def prepare_offline_skin(self, player_name: str, player_uuid: str) -> Optional[dict]:
        '''准备本地皮肤服务，返回 {"api": url, "skin": path, "model": ...}。'''

        settings = self.get_settings()
        mode = settings['mode']
        model = settings.get('model', 'auto')
        skin_path = None
        if mode in ('random', 'steve', 'alex'):
            if mode == 'random':
                chosen = random.choice(['steve', 'alex'])
            else:
                chosen = mode
            model = 'slim' if chosen == 'alex' else 'classic'
            skin_path = self._default_skin(model)
        elif mode == 'premium':
            name = (settings.get('premium_name') or player_name or '').strip()
            if not name:
                return
            safe = re.sub('[^A-Za-z0-9_\\-]', '_', name)[:32]
            skin_path = self._download_skin_candidates([PREMIUM_URL.format(name=name), MINOTAR_SKIN_URL.format(name=name)], f'premium_{safe}.png')
            if skin_path is None:
                skin_path = self._default_skin('classic')
            model = 'slim' if model == 'slim' else 'classic'
        elif mode == 'custom':
            path = self._resolve_custom_path(settings.get('custom_path', ''))
            if not path.is_file():
                return
            skin_path = path
            model = detect_skin_model(path)
        else:
            return
        if skin_path is None:
            return
        cape_path = self.create_cape_texture(settings.get('cape', '无披风'))
        server = LocalSkinServer.instance()
        server.register(player_uuid, player_name, skin_path, model, cape_path)
        api = server.ensure_started()
        if not api:
            return
        return {
            'api': api,
            'skin': str(skin_path),
            'model': model,
            'cape': str(cape_path) if cape_path else '',
        }

    def preview_skin(self, settings: dict) -> Optional[Path]:
        '''获取用于界面预览的皮肤文件（必要时下载）。'''

        mode = settings.get('mode', 'random')
        if mode in ('random', 'steve', 'alex'):
            chosen = random.choice(['steve', 'alex']) if mode == 'random' else mode
            if chosen == 'alex':
                return self._default_skin('slim')
            return self._default_skin('classic')
        if mode == 'premium':
            name = (settings.get('premium_name') or '').strip()
            if not name:
                return
            safe = re.sub('[^A-Za-z0-9_\\-]', '_', name)[:32]
            cached = self.dir / f'premium_{safe}.png'
            if cached.exists() and cached.stat().st_size > 64:
                return cached
            return self._download_skin_candidates([PREMIUM_URL.format(name=name), MINOTAR_SKIN_URL.format(name=name)], f'premium_{safe}.png')
        if mode == 'custom':
            path = self._resolve_custom_path(settings.get('custom_path', ''))
            if path.is_file():
                return path

    def preview_path(self) -> Optional[Path]:
        settings = self.get_settings()
        mode = settings['mode']
        if mode == 'random' or mode == 'steve':
            return self._default_skin('classic')
        if mode == 'alex':
            return self._default_skin('slim')
        if mode == 'custom':
            path = self._resolve_custom_path(settings.get('custom_path', ''))
            if path.is_file():
                return path
            return
        if mode == 'premium':
            name = (settings.get('premium_name') or '').strip()
            if not name:
                return
            safe = re.sub('[^A-Za-z0-9_\\-]', '_', name)[:32]
            path = self.dir / f'premium_{safe}.png'
            if path.is_file():
                return path


# WARNING: Decompyle incomplete
