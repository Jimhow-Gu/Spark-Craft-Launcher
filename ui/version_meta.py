'''版本名称、描述与图标的统一解析。'''

from __future__ import annotations

import json
import re
from pathlib import Path

from core.version import detect_version_loader

TYPE_LABELS = {
    'release': '正式版',
    'snapshot': '快照版',
    'old_beta': '旧版 Beta',
    'old_alpha': '旧版 Alpha',
}
LOADER_LABELS = {
    'fabric': 'Fabric',
    'forge': 'Forge',
    'neoforge': 'NeoForge',
    'quilt': 'Quilt',
    'optifine': 'OptiFine',
}
ICON_FILES = {
    'release': 'Release.png',
    'snapshot': 'Snapshoot.png',
    'fabric': 'fabric.png',
    'forge': 'forge.png',
    'neoforge': 'neo_forge.png',
    'optifine': 'optifine.png',
}

_IMAGES_DIR = Path(__file__).resolve().parent / 'imgs'


def version_tuple(text) -> tuple:
    '''把 1.21.1 / 1.20 这类版本号转成可比较的元组。'''

    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', str(text or ''))
    if not match:
        return ()
    return tuple(int(part) for part in match.groups() if part)


def loader_supported(mc_version, loader_id) -> bool:
    '''与下载页保持一致：判断某个加载器能否安装到该 Minecraft 版本。'''

    version = version_tuple(mc_version)
    if not version:
        return True
    if loader_id in ('fabric', 'fabric_api', 'optifabric'):
        return version >= (1, 14)
    if loader_id == 'forge':
        return version >= (1, 5, 2)
    if loader_id == 'neoforge':
        return version >= (1, 20)
    if loader_id == 'optifine':
        return version >= (1, 7, 2)
    return True


def version_settings(config, version_id):
    settings = config.get('version_settings', {}) or {}
    return settings.get(version_id, {}) or {}


def display_name(config, version_id):
    nicknames = config.get('version_nicknames', {}) or {}
    return str(nicknames.get(version_id) or version_id)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}


def version_data(mc_dir, version_id):
    version_dir = Path(mc_dir) / 'versions' / str(version_id)
    return _read_json(version_dir / f'{version_id}.json')


def version_description(mc_dir, version_id, config=None, version_type='', loader=''):
    settings = version_settings(config, version_id) if config is not None else {}
    custom = str(settings.get('description') or '').strip()
    if custom:
        return custom
    version_dir = Path(mc_dir) / 'versions' / str(version_id)
    data = _read_json(version_dir / f'{version_id}.json')
    pack = _read_json(version_dir / 'pmgl_pack.json')
    base = str(pack.get('mc_version') or data.get('inheritsFrom') or version_id)
    active_loader = str(pack.get('loader') or loader or '').lower()
    if not active_loader:
        try:
            active_loader = detect_version_loader(version_id, data, version_dir)
        except Exception:
            active_loader = ''
    if not active_loader:
        hint = f"{data.get('inheritsFrom') or ''} {data.get('jar') or ''}".lower()
        for key in ('neoforge', 'forge', 'fabric', 'quilt', 'optifine'):
            if key in hint:
                active_loader = key
                break
    loader_version = str(pack.get('loader_version') or '')
    if not loader_version and active_loader:
        patterns = {
            'fabric': r'fabric-loader:([^:@]+)',
            'forge': r'minecraftforge:forge:([^:@]+)',
            'neoforge': r'neoforged:neoforge:([^:@]+)',
            'quilt': r'quilt-loader:([^:@]+)',
            'optifine': r'optifine:OptiFine:([^:@]+)',
        }
        pattern = patterns.get(active_loader)
        if pattern:
            for lib in data.get('libraries') or []:
                name = str((lib or {}).get('name') or '')
                match = re.search(pattern, name, re.I)
                if match:
                    loader_version = match.group(1)
                    break
    if active_loader == 'forge' and loader_version.startswith(base + '-'):
        loader_version = loader_version[len(base) + 1:]
    type_key = str(version_type or data.get('type') or '').lower()
    if type_key in ('', 'unknown'):
        type_key = 'release'
    text = f'{TYPE_LABELS.get(type_key, "未知")} {base}'
    if active_loader:
        loader_label = LOADER_LABELS.get(active_loader, active_loader)
        text += f', {loader_label} {loader_version}'.rstrip()
    return text


def version_icon(mc_dir, version_id, config=None, version_type='', loader=''):
    settings = version_settings(config, version_id) if config is not None else {}
    icon_value = str(settings.get('icon') or 'auto')
    if icon_value and icon_value not in ('auto', 'custom'):
        candidate = Path(icon_value)
        if candidate.is_file():
            return candidate
        filename = ICON_FILES.get(icon_value.lower())
        if filename:
            path = _IMAGES_DIR / filename
            if path.is_file():
                return path
    version_dir = Path(mc_dir) / 'versions' / str(version_id)
    for relative in ('icon.png', 'pack.png', 'logo.png', 'loading/logo.png'):
        path = version_dir / relative
        if path.is_file():
            return path
    data = _read_json(version_dir / f'{version_id}.json')
    active_loader = str(loader or '').lower()
    if not active_loader:
        try:
            active_loader = detect_version_loader(version_id, data, version_dir)
        except Exception:
            active_loader = ''
    if not active_loader:
        hint = f"{data.get('inheritsFrom') or ''} {data.get('jar') or ''}".lower()
        for key in ('neoforge', 'forge', 'fabric', 'quilt', 'optifine'):
            if key in hint:
                active_loader = key
                break
    filename = ICON_FILES.get(active_loader)
    if not filename:
        vtype = str(version_type or data.get('type') or '').lower()
        filename = 'Snapshoot.png' if vtype == 'snapshot' else 'Release.png'
    path = _IMAGES_DIR / filename
    return path if path.is_file() else None


def default_icon_value(mc_dir, version_id, config=None):
    '''未设置图标时，应根据版本类型默认选中的图标项。'''

    settings = version_settings(config, version_id) if config is not None else {}
    category = str(settings.get('category') or '').lower()
    version_dir = Path(mc_dir) / 'versions' / str(version_id)
    data = _read_json(version_dir / f'{version_id}.json')
    active_loader = ''
    try:
        active_loader = detect_version_loader(version_id, data, version_dir)
    except Exception:
        active_loader = ''
    if active_loader in ICON_FILES:
        return active_loader
    if category == 'modpack':
        return 'release'
    version_type = str(data.get('type') or 'release').lower()
    return 'snapshot' if version_type == 'snapshot' else 'release'


def detect_installed_loader(mc_dir, version_id):
    '''沿继承链识别实例实际使用的加载器。'''

    current = str(version_id)
    seen = set()
    while current and current not in seen:
        seen.add(current)
        version_dir = Path(mc_dir) / 'versions' / current
        data = _read_json(version_dir / f'{current}.json')
        try:
            loader = detect_version_loader(current, data, version_dir)
        except Exception:
            loader = ''
        if loader:
            return loader
        current = str(data.get('inheritsFrom') or '').strip()
    return ''
