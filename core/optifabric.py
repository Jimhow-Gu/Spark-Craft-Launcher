'''OptiFabric 自动获取与下载（CurseForge 公开元数据，无需 API Key）。'''

from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import (Callable, Dict, List, Optional)
from urllib.parse import quote
from core.net import (DownloadCancelled, DownloadError, HttpClient)
from core.paths import scl_dir
logger = logging.getLogger('OptiFabric')
CFWIDGET_URL = 'https://api.cfwidget.com/322385'
BMCLAPI = 'https://bmclapi2.bangbang93.com'

# Fabric Loader 0.15 起移除了内置 tinyremapper，
# 旧版 OptiFabric（< 1.13.25）需要搭配旧版 Loader（如 0.14.25）
OPTIFABRIC_LOADER_COMPAT = (1, 13, 25)
LEGACY_FABRIC_LOADER = '0.14.25'


def file_version_tuple(file):
    text = f"{(file or {}).get('version', '')} {(file or {}).get('filename', '')}"
    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', str(text))
    if not match:
        return ()
    return tuple(int(part) for part in match.groups() if part)


def mc_version_tuple(text):
    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', str(text or ''))
    if not match:
        return ()
    return tuple(int(part) for part in match.groups() if part)


def loader_version_tuple(text):
    match = re.match(r'(\d+)\.(\d+)(?:\.(\d+))?', str(text or '').strip())
    if not match:
        return ()
    return tuple(int(part) for part in match.groups() if part)


def resolve(mc_version, chosen=None):
    '''返回 (OptiFabric 文件或 None, 是否需要旧版 Fabric Loader)。

    chosen 为下载页用户明确选择的文件时以其版本为准；
    否则取该 MC 版本最新的 OptiFabric。
    '''

    if isinstance(chosen, dict) and chosen:
        return chosen, file_version_tuple(chosen) < OPTIFABRIC_LOADER_COMPAT
    files = [item for item in fetch_versions(mc_version) if item]
    if not files:
        return None, False
    newest = files[0]
    return newest, file_version_tuple(newest) < OPTIFABRIC_LOADER_COMPAT

def fetch_versions(mc_version):
    '''获取支持指定 Minecraft 版本的 OptiFabric 文件，最新优先。'''

    mc_version = str(mc_version or '').strip()
    if not mc_version:
        return []
    try:
        data = HttpClient(timeout=30).get_json(CFWIDGET_URL)
    except Exception as exc:
        logger.warning(f'获取 OptiFabric 版本失败: {exc}')
        return []
    if not isinstance(data, dict):
        return []
    result = []
    for item in data.get('files') or []:
        if not isinstance(item, dict):
            continue
        versions = [str(v) for v in item.get('versions') or []]
        if mc_version not in versions or 'Fabric' not in versions:
            continue
        filename = str(item.get('name') or '').strip()
        if not filename.lower().endswith('.jar'):
            continue
        try:
            file_id = int(item.get('id'))
        except (TypeError, ValueError):
            pass
        else:
            folder, rest = divmod(file_id, 1000)
            encoded = quote(filename)
            urls = [f'https://mediafilez.forgecdn.net/files/{folder}/{rest}/{encoded}', f'https://edge.forgecdn.net/files/{folder}/{rest}/{encoded}', f'{BMCLAPI}/files/{folder}/{rest}/{encoded}']
            result.append({'id': str(item.get('id')), 'version': str(item.get('display') or filename), 'filename': filename, 'url': urls[0], 'urls': urls, 'size': int(item.get('filesize') or 0), 'date': str(item.get('uploaded_at') or ''), 'release_type': str(item.get('type') or 'release'), 'game_versions': versions})
    result.sort(key=(lambda f: (f.get('date', ''), f.get('id', ''))), reverse=True)
    return result

def find_for(mc_version):
    versions = fetch_versions(mc_version)
    if versions:
        return versions[0]

def download(file, version_dir, *, cancel=(lambda: False)):
    '''下载 OptiFabric 到实例的 mods 目录。'''

    mods_dir = Path(version_dir) / 'mods'
    mods_dir.mkdir(parents=True, exist_ok=True)
    dest = mods_dir / str(file.get('filename') or 'optifabric.jar')
    last_error = None
    for url in file.get('urls') or [file.get('url')]:
        if not url:
            continue
        try:
            HttpClient(timeout=60).download(url, dest, size=file.get('size') or None, cancel=cancel, allow_slow_fallback=False)
            if dest.is_file() and dest.stat().st_size > 1024:
                return dest
        except DownloadCancelled:
            raise
        except Exception as exc:
            last_error = exc
            continue
    raise DownloadError(f'OptiFabric 下载失败: {last_error}')

def cache_dir():
    path = scl_dir() / 'cache' / 'optifabric'
    path.mkdir(parents=True, exist_ok=True)
    return path

