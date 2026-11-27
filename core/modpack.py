'''
整合包导入引擎。

支持格式（参考 HMCL 的 ModpackProvider 多适配器设计与安装流水线，独立实现）：
- Modrinth .mrpack（modrinth.index.json + overrides/client-overrides）
- CurseForge / FTB .zip（manifest.json + minecraft + files + overrides）
- MultiMC / Prism .zip（instance.cfg + mmc-pack.json + .minecraft/）
- HMCL / MCBBS 导出包（manifest.json + files[].path/downloads/hash + overrides）

导入流程：识别格式 -> 安装原版与加载器 -> 下载文件并校验 SHA-1 ->
解压覆盖文件 -> 写入 pmgl_pack.json（供后续补全/启动校验）-> 清理未完成标记。
'''

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import List, Optional
from urllib.parse import quote

from PySide6.QtCore import QThread, Signal

from core.config import ConfigManager
from core.paths import safe_rmtree
from core.downloading import DownloadClient, RemoteFile
from core.installers import (InstallCancelled, ProgressMapper, create_alias, install_fabric, install_forge, install_neoforge, install_quilt, install_vanilla)
from core.mods import CurseForgeSource, pcl_mod_mirror
from core.net import (
    DownloadCancelled, DownloadError, DownloadGate, HttpClient,
)
from core.version import (
    INCOMPLETE_MARKER, detect_version_loader, register_active_install,
    unregister_active_install,
)


@dataclass
class ModpackFile:
    path: str = ''
    urls: List[str] = field(default_factory=list)
    sha1: str = ''
    size: Optional[int] = None
    required: bool = True
    env_client: str = ''
    project_id: str = ''
    file_id: str = ''
    file_name: str = ''


@dataclass
class ModpackInfo:
    path: Path
    kind: str
    name: str
    version: str
    mc_version: str
    loader_id: str
    loader_version: str
    summary: str = ''
    file_count: int = 0
    author: str = ''
    overrides: List[str] = field(default_factory=list)
    files: List[ModpackFile] = field(default_factory=list)
    root_prefix: str = ''
    icon_prefixes: List[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def format_name(self):
        return {'mrpack': 'Modrinth', 'curseforge': 'CurseForge', 'multimc': 'MultiMC/Prism', 'hmcl': 'HMCL', 'mcbbs': 'MCBBS'}.get(self.kind, self.kind)


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_ICON_BYTES = 4 * 1024 * 1024


def _read_json(zf, name, max_bytes=MAX_MANIFEST_BYTES):
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise DownloadError(f'整合包缺少文件: {name}')
    if int(info.file_size or 0) > max_bytes:
        raise DownloadError(f'整合包元数据过大: {name}')
    try:
        return json.loads(zf.read(name).decode('utf-8-sig'))
    except RecursionError as exc:
        raise DownloadError(f'整合包元数据嵌套过深: {name}') from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadError(f'整合包元数据格式异常: {name}') from exc


def _entry_map(zf):
    return {name.replace('\\', '/'): name for name in zf.namelist()}


def _find_entry(names, filename, root_only=True):
    lowered = {name.lower(): name for name in names}
    if filename.lower() in lowered:
        return lowered[filename.lower()]
    if root_only:
        return ''
    matches = [name for name in names if name.lower().endswith('/' + filename.lower()) and name.count('/') == 1]
    return matches[0] if len(matches) == 1 else ''


def _loader_from_id(loader_id):
    lid = str(loader_id or '').strip()
    lowered = lid.lower()
    for prefix, key in (('fabric-loader-', 'fabric'), ('quilt-loader-', 'quilt'), ('neoforge-', 'neoforge'), ('forge-', 'forge'), ('fabric-', 'fabric'), ('quilt-', 'quilt')):
        if lowered.startswith(prefix):
            return key, lid[len(prefix):]
    if lowered in ('forge', 'neoforge', 'fabric', 'quilt'):
        return lowered, ''
    return '', ''


def _loader_from_manifest(loaders):
    entries = []
    if isinstance(loaders, dict):
        loaders = [loaders]
    if not isinstance(loaders, (list, tuple)):
        return '', ''
    for index, loader in enumerate(loaders):
        if isinstance(loader, dict):
            lid = loader.get('id') or loader.get('loader') or ''
            primary = bool(loader.get('primary'))
        else:
            lid = loader
            primary = False
        key, version = _loader_from_id(lid)
        if key:
            entries.append((primary, index, key, version))
    for primary in (True, False):
        for is_primary, _index, key, version in entries:
            if is_primary == primary:
                return key, version
    return '', ''


def _loader_from_deps(deps):
    for key, loader in (('neoforge', 'neoforge'), ('forge', 'forge'), ('fabric-loader', 'fabric'), ('quilt-loader', 'quilt')):
        if key in deps:
            return loader, str(deps.get(key) or '')
    return '', ''


WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    *{f'COM{i}' for i in range(1, 10)},
    *{f'LPT{i}' for i in range(1, 10)},
}


def is_windows_reserved_name(name):
    stem = str(name or '').split('.', 1)[0].strip().upper()
    return stem in WINDOWS_RESERVED_NAMES


def sanitize_version_name(name):
    name = (name or '').strip()
    name = ''.join('-' if (ord(ch) < 32 or ch in '<>:"/\|?*') else ch for ch in name)
    name = re.sub(r'\s+', ' ', name).strip(' .')
    if not name:
        return ''
    if is_windows_reserved_name(name):
        stem, dot, ext = name.partition('.')
        name = f'{stem}-pack{dot}{ext}'
    # 超长名称附加短哈希，避免两个不同名称截断后静默撞名。
    if len(name) > 80:
        digest = hashlib.sha1(name.encode('utf-8')).hexdigest()[:6]
        name = name[:73].rstrip(' .') + '-' + digest
    return name[:80].strip(' .')


def _safe_join(base, relative):
    rel = PurePosixPath(str(relative).replace('\\', '/'))
    if rel.is_absolute() or '..' in rel.parts:
        raise DownloadError(f'整合包内存在非法路径: {relative}')
    target = Path(base) / Path(*rel.parts)
    base_abs = os.path.normcase(os.path.abspath(str(base)))
    target_abs = os.path.normcase(os.path.abspath(str(target)))
    try:
        if os.path.commonpath([base_abs, target_abs]) != base_abs:
            raise ValueError
    except ValueError:
        raise DownloadError(f'整合包内存在非法路径: {relative}')
    return target


def _format_speed(bytes_per_second):
    value = max(0.0, float(bytes_per_second or 0))
    if value >= 1048576:
        return f'{value / 1048576:.1f} MB/s'
    if value >= 1024:
        return f'{value / 1024:.1f} KB/s'
    return f'{int(value)} B/s'


def _forgecdn_candidates(url):
    if not url:
        return []
    result = []
    if 'forgecdn.net' in url:
        path = url.split('forgecdn.net', 1)[1]
        for host in ('https://mediafilez.forgecdn.net', 'https://edge.forgecdn.net'):
            candidate = host + path
            if candidate not in result:
                result.append(candidate)
    if url not in result:
        result.append(url)
    # Modrinth/ForgeCDN 都生成 mcimirror 候选；镜像优先由社区源设置决定。
    mirrored = pcl_mod_mirror(url)
    if mirrored and mirrored != url and mirrored not in result:
        try:
            from core.config import ConfigManager
            pref = str(
                ConfigManager().get('community_source_pref', 'mirror') or 'mirror'
            ).lower()
        except Exception:
            pref = 'mirror'
        if pref in ('mirror', 'auto'):
            result.insert(0, mirrored)
        else:
            result.append(mirrored)
    return result


def _count_entries(names, prefixes=()):
    count = 0
    for name in names:
        if name.endswith('/'):
            continue
        if not prefixes or any(name.startswith(prefix) for prefix in prefixes):
            count += 1
    return count


def inspect_modpack(path):
    path = Path(path)
    if not path.is_file():
        raise DownloadError('整合包文件不存在')
    if not zipfile.is_zipfile(path):
        raise DownloadError('不是有效的整合包（需要 .mrpack 或 .zip）')
    with zipfile.ZipFile(path) as zf:
        names = list(zf.namelist())
        normalized = [name.replace('\\', '/') for name in names]
        # 与 PCL CE 的检测顺序保持一致：
        # MCBBS -> MultiMC -> Modrinth -> CurseForge/MCBBS manifest -> HMCL。
        entry = _find_entry(normalized, 'mcbbs.packmeta', root_only=False)
        if entry:
            return _inspect_mcbbs_pack(path, zf, entry, normalized)
        entry = _find_entry(normalized, 'mmc-pack.json', root_only=False)
        if entry:
            return _inspect_multimc(path, zf, entry, normalized)
        entry = _find_entry(normalized, 'modrinth.index.json', root_only=False)
        if entry:
            return _inspect_modrinth(path, zf, entry, normalized)
        entry = _find_entry(normalized, 'manifest.json', root_only=False)
        if entry:
            return _inspect_manifest_pack(path, zf, entry, normalized)
        entry = _find_entry(normalized, 'modpack.json', root_only=False)
        if entry:
            return _inspect_hmcl_pack(path, zf, entry, normalized)
    raise DownloadError('无法识别的整合包格式（支持 Modrinth、CurseForge/FTB、MultiMC/Prism、HMCL/MCBBS 导出包）')


def _pack_root(entry):
    return entry.rsplit('/', 1)[0] + '/' if '/' in entry else ''


def _inspect_modrinth(path, zf, entry, names):
    index = _read_json(zf, entry)
    deps = index.get('dependencies') or {}
    if not deps.get('minecraft'):
        raise DownloadError('Modrinth 整合包缺少 minecraft 依赖版本')
    loader_id, loader_version = _loader_from_deps(deps)
    files = []
    for item in index.get('files') or []:
        if not isinstance(item, dict):
            continue
        env = item.get('env') or {}
        files.append(ModpackFile(
            path=str(item.get('path') or ''),
            urls=[u for u in item.get('downloads') or [] if isinstance(u, str)],
            sha1=str((item.get('hashes') or {}).get('sha1') or ''),
            size=item.get('fileSize'),
            required=(env.get('client') or '').lower() not in ('unsupported', 'optional'),
            env_client=str(env.get('client') or ''),
        ))
    prefixes = ['overrides/', 'client-overrides/']
    return ModpackInfo(
        path=path, kind='mrpack', name=str(index.get('name') or path.stem),
        version=str(index.get('versionId') or ''), mc_version=str(deps.get('minecraft') or ''),
        loader_id=loader_id, loader_version=loader_version,
        summary=str(index.get('summary') or ''),
        file_count=len(files) + _count_entries(names, prefixes),
        overrides=prefixes, files=files, icon_prefixes=[''] + prefixes,
    )


_MULTIMC_COMPONENTS = (
    ('net.minecraft', 'game'),
    ('net.minecraftforge', 'forge'),
    ('net.neoforged', 'neoforge'),
    ('net.fabricmc.fabric-loader', 'fabric'),
    ('org.quiltmc.quilt-loader', 'quilt'),
    ('com.mumfrey.liteloader', 'liteloader'),
)


def _inspect_multimc(path, zf, entry, names):
    root = _pack_root(entry)
    manifest = _read_json(zf, entry)
    components = manifest.get('components') or []
    versions = {}
    for component in components:
        if not isinstance(component, dict):
            continue
        uid = str(component.get('uid') or '')
        version = str(component.get('version') or '')
        if uid and version:
            versions[uid] = version
    mc_version = versions.get('net.minecraft', '')
    if not mc_version:
        raise DownloadError('MultiMC 整合包缺少 net.minecraft 组件版本')
    loader_id = loader_version = ''
    for uid, key in _MULTIMC_COMPONENTS[1:]:
        if versions.get(uid):
            loader_id, loader_version = key, versions[uid]
            break
    name = path.stem
    icon_key = ''
    instance_entry = _find_entry(names, 'instance.cfg', root_only=False)
    if instance_entry:
        try:
            text = zf.read(instance_entry).decode('utf-8', 'replace')
            match = re.search(r'^name=(.+)$', text, re.M)
            if match:
                name = match.group(1).strip()
            match = re.search(r'^iconKey=(.+)$', text, re.M)
            if match:
                icon_key = match.group(1).strip()
        except (OSError, KeyError):
            pass
    prefixes = [root + '.minecraft/', root + 'minecraft/']
    return ModpackInfo(
        path=path, kind='multimc', name=name, version='',
        mc_version=mc_version, loader_id=loader_id, loader_version=loader_version,
        summary='MultiMC / Prism 实例',
        file_count=_count_entries(names, prefixes) + _count_entries(names, [root]),
        overrides=prefixes,
        icon_prefixes=[root, root + '.minecraft/', root + 'minecraft/'],
        extra={'icon_key': icon_key},
    )


def _loader_from_addons(addons):
    values = {}
    for item in addons or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get('id') or '').strip().lower()
        if key:
            values[key] = str(item.get('version') or '').strip()
    mc_version = values.get('game', '')
    for key in ('forge', 'neoforge', 'fabric', 'quilt'):
        if values.get(key):
            return mc_version, key, values[key]
    return mc_version, '', ''


def _mcbbs_files(items):
    files = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get('path') or '').strip()
        urls = []
        for value in item.get('downloads') or []:
            if isinstance(value, str) and value and value not in urls:
                urls.append(value)
        for value in item.get('urls') or []:
            if isinstance(value, str) and value and value not in urls:
                urls.append(value)
        url = str(item.get('url') or '').strip()
        if url and url not in urls:
            urls.insert(0, url)
        project_id = str(item.get('projectID') or item.get('projectId') or '')
        file_id = str(item.get('fileID') or item.get('fileId') or '')
        # addon 类型文件通常已包含在 overrides 中，没有独立下载地址；
        # 这里只记录可下载的文件，避免把内嵌文件误报为下载失败。
        if not urls and not (project_id and file_id):
            continue
        files.append(ModpackFile(
            path=path,
            urls=urls,
            sha1=str(item.get('hash') or item.get('sha1') or ''),
            size=item.get('size') or item.get('fileSize'),
            required=bool(item.get('force', True)),
            project_id=project_id,
            file_id=file_id,
            file_name=str(item.get('fileName') or ''),
        ))
    return files


def _mcbbs_info(path, data, names, root=''):
    mc_version, loader_id, loader_version = _loader_from_addons(data.get('addons') or [])
    if not mc_version:
        raise DownloadError('MCBBS 整合包缺少 game 组件版本')
    overrides = str(data.get('overrides') or 'overrides').replace('\\', '/').strip('/')
    prefixes = [root + overrides + '/', root + 'overrides/']
    prefixes = list(dict.fromkeys(prefixes))
    files = _mcbbs_files(data.get('files') or [])
    return ModpackInfo(
        path=path, kind='mcbbs',
        name=str(data.get('name') or path.stem),
        version=str(data.get('version') or ''),
        mc_version=mc_version,
        loader_id=loader_id, loader_version=loader_version,
        summary=str(data.get('description') or data.get('author') or ''),
        file_count=max(len(files), _count_entries(names, prefixes)),
        author=str(data.get('author') or ''),
        overrides=prefixes, files=files, icon_prefixes=[root] + prefixes,
        extra={'file_api': str(data.get('fileApi') or '')},
    )


def _inspect_mcbbs_pack(path, zf, entry, names):
    data = _read_json(zf, entry)
    if not isinstance(data, dict):
        raise DownloadError('MCBBS 整合包元数据无效')
    return _mcbbs_info(path, data, names, _pack_root(entry))


def _inspect_hmcl_pack(path, zf, entry, names):
    data = _read_json(zf, entry)
    if not isinstance(data, dict):
        raise DownloadError('HMCL 整合包元数据无效')
    root = _pack_root(entry)
    mc_version = str(data.get('gameVersion') or data.get('mcVersion') or '')
    pack_entry = root + 'minecraft/pack.json'
    if pack_entry in names:
        try:
            pack = _read_json(zf, pack_entry)
            jar = pack.get('jar')
            if isinstance(jar, dict):
                mc_version = mc_version or str(jar.get('id') or '')
            elif isinstance(jar, str):
                mc_version = mc_version or jar
        except DownloadError:
            pass
    loader_id = loader_version = ''
    loader = data.get('loader')
    if isinstance(loader, dict):
        loader_id = str(loader.get('type') or loader.get('id') or '')
        loader_version = str(loader.get('version') or '')
    elif isinstance(loader, str) and loader.strip():
        loader_id = loader.strip()
    if not loader_id:
        loader_id, loader_version = _loader_from_manifest(
            data.get('modLoaders') or []
        )
    else:
        if not loader_version:
            loader_version = str(data.get('loaderVersion') or '')
        parsed_id, parsed_version = _loader_from_id(loader_id)
        if parsed_id:
            loader_id = parsed_id
            loader_version = loader_version or parsed_version
    prefix = root + 'minecraft/'
    return ModpackInfo(
        path=path, kind='hmcl',
        name=str(data.get('name') or path.stem),
        version=str(data.get('version') or ''),
        mc_version=mc_version,
        loader_id=loader_id, loader_version=loader_version,
        summary=str(data.get('description') or data.get('author') or ''),
        file_count=_count_entries(names, [prefix]),
        author=str(data.get('author') or ''),
        overrides=[prefix], icon_prefixes=[root, prefix],
    )


def _inspect_manifest_pack(path, zf, entry, names):
    manifest = _read_json(zf, entry)
    if not isinstance(manifest, dict):
        raise DownloadError('manifest.json 内容无效')
    # PCL CE 通过 addons 字段区分 MCBBS 与 CurseForge manifest。
    if isinstance(manifest.get('addons'), list):
        return _mcbbs_info(path, manifest, names, _pack_root(entry))
    files_raw = [item for item in manifest.get('files') or [] if isinstance(item, dict)]
    minecraft = manifest.get('minecraft') if isinstance(manifest.get('minecraft'), dict) else {}
    # HMCL/PCL 导出的 manifest 也可能带 minecraft 元数据，但文件项使用
    # path + downloads；只有 CurseForge 使用 projectID/fileID。不能只凭
    # minecraft 字段就判定为 CurseForge，否则路径型整合包会被识别错。
    has_explicit_paths = any(str(item.get('path') or '').strip() for item in files_raw)
    has_project_ids = any(
        str(item.get('projectID') or item.get('fileID') or '').strip()
        for item in files_raw
    )
    is_curse = (not has_explicit_paths) and (has_project_ids or bool(minecraft))
    if is_curse:
        mc_version = str(minecraft.get('version') or '')
        loader_id, loader_version = _loader_from_manifest(minecraft.get('modLoaders') or [])
        files = []
        for item in files_raw:
            project_id = str(item.get('projectID') or '')
            file_id = str(item.get('fileID') or '')
            file_name = str(item.get('fileName') or '')
            url = str(item.get('url') or item.get('downloadUrl') or '')
            urls = [url] if url else []
            urls += [u for u in item.get('urls') or [] if isinstance(u, str)]
            files.append(ModpackFile(
                path='',
                urls=urls,
                sha1=str((item.get('hashes') or {}).get('sha1') or item.get('sha1') or ''),
                size=item.get('fileSize') or item.get('size'),
                required=bool(item.get('required', True)),
                project_id=project_id, file_id=file_id, file_name=file_name,
            ))
        overrides = str(manifest.get('overrides') or 'overrides')
        prefixes = [overrides.rstrip('/') + '/'] if overrides else []
        if 'overrides/' not in prefixes:
            prefixes.append('overrides/')
        return ModpackInfo(
            path=path, kind='curseforge', name=str(manifest.get('name') or path.stem),
            version=str(manifest.get('version') or ''), mc_version=mc_version,
            loader_id=loader_id, loader_version=loader_version,
            summary=str(manifest.get('author') or ''),
            file_count=len(files) + _count_entries(names, prefixes),
            author=str(manifest.get('author') or ''), overrides=prefixes,
            files=files, icon_prefixes=[''] + prefixes,
        )
    # HMCL / MCBBS：manifestType = minecraftModpack，files[].path + downloads
    files = []
    for item in files_raw:
        rel = str(item.get('path') or '')
        if not rel:
            continue
        urls = [u for u in item.get('downloads') or [] if isinstance(u, str)]
        url = str(item.get('url') or '')
        if url:
            urls.insert(0, url)
        files.append(ModpackFile(
            path=rel, urls=urls,
            sha1=str(item.get('hash') or item.get('sha1') or ''),
            size=item.get('size') or item.get('fileSize'),
            required=bool(item.get('required', True)),
        ))
    mc_version = str(
        manifest.get('gameVersion')
        or manifest.get('mcVersion')
        or minecraft.get('version')
        or ''
    )
    loader_id, loader_version = _loader_from_manifest(
        minecraft.get('modLoaders') or []
    )
    overrides = str(manifest.get('overrides') or 'overrides')
    prefixes = [overrides.rstrip('/') + '/'] if overrides else []
    if 'overrides/' not in prefixes:
        prefixes.append('overrides/')
    return ModpackInfo(
        path=path, kind='hmcl', name=str(manifest.get('name') or path.stem),
        version=str(manifest.get('version') or ''), mc_version=mc_version,
        loader_id=loader_id, loader_version=loader_version,
        summary=str(manifest.get('description') or manifest.get('author') or ''),
        file_count=len(files) + _count_entries(names, prefixes),
        author=str(manifest.get('author') or ''),
        overrides=prefixes, files=files, icon_prefixes=[''] + prefixes,
        extra={'file_api': str(manifest.get('fileApi') or '')},
    )


class ModpackImportWorker(QThread):
    status_updated = Signal(str)
    progress_updated = Signal(int)
    stats_updated = Signal(str, str)
    log_message = Signal(str)
    finished_ok = Signal(str)
    finished_error = Signal(str)

    def __init__(
        self, path, mc_dir, pack_name='', parent=None,
        existing_version='', icon_url='',
    ):
        super().__init__(parent)
        self.path = str(path)
        self.mc_dir = str(Path(mc_dir))
        self.pack_name = pack_name
        self.existing_version = str(existing_version or '').strip()
        self.icon_url = str(icon_url or '').strip()
        self._created_target = not bool(self.existing_version)
        self._cancelled = False
        # 整合包内大文件（100MB+ 的模组/资源包）在慢速网络下极易超过 90 秒，
        # 这里放宽到 15 分钟；真正的卡死仍由 20 秒读超时和用户取消处理。
        self._file_deadline = 900
        self._alias_id = ''
        self._config = ConfigManager()
        self._before_dirs = None
        self._install_token = f'modpack-{id(self)}-{int(time.time() * 1000)}'
        self._last_status_emit = 0.0
        self._last_log_emit = 0.0

    def _throttled_status(self, text):
        now = time.monotonic()
        if now - self._last_status_emit < 0.12:
            return
        self._last_status_emit = now
        self.status_updated.emit(str(text))

    def _throttled_log(self, text):
        now = time.monotonic()
        if now - self._last_log_emit < 0.15:
            return
        self._last_log_emit = now
        self.log_message.emit(str(text))

    def cancel(self):
        self._cancelled = True

    def _check_cancel(self):
        if self._cancelled:
            raise InstallCancelled()

    def _verify_existing_target(self, info, target_dir, name):
        try:
            data = json.loads(
                (target_dir / f'{name}.json').read_text(encoding='utf-8')
            )
        except (OSError, ValueError) as exc:
            raise DownloadError(f'无法读取目标实例配置：{exc}') from exc
        loader = detect_version_loader(name, data, target_dir)
        if info.loader_id:
            if not loader:
                raise DownloadError(
                    f'目标实例「{name}」没有可用的 Mod 加载器，'
                    f'不能安装 {info.loader_id} 整合包'
                )
            if loader.lower() != str(info.loader_id).lower():
                raise DownloadError(
                    f'目标实例加载器为 {loader}，与整合包的 '
                    f'{info.loader_id} 不一致'
                )
        target_mc = str(data.get('clientVersion') or '').strip()
        if target_mc and info.mc_version and target_mc != info.mc_version:
            raise DownloadError(
                f'目标实例 Minecraft 版本为 {target_mc}，'
                f'与整合包的 {info.mc_version} 不一致'
            )

    def run(self):
        alias_dir = None
        try:
            info = inspect_modpack(self.path)
            if not info.mc_version:
                raise DownloadError('整合包未包含 Minecraft 版本信息，无法自动安装。请手动创建版本后导入文件。')
            versions_dir = Path(self.mc_dir) / 'versions'
            versions_dir.mkdir(parents=True, exist_ok=True)
            if self.existing_version:
                name = self.existing_version
                target_dir = versions_dir / name
                if (
                    not target_dir.is_dir()
                    or not (target_dir / f'{name}.json').is_file()
                ):
                    raise DownloadError(f'目标版本不存在：{name}')
                if (target_dir / INCOMPLETE_MARKER).exists():
                    raise DownloadError(f'目标版本尚未安装完成：{name}')
                self._verify_existing_target(info, target_dir, name)
                self._created_target = False
            else:
                name = sanitize_version_name(self.pack_name or info.name or Path(self.path).stem) or '整合包'
                target_dir = versions_dir / name
                if target_dir.exists():
                    if (target_dir / INCOMPLETE_MARKER).exists():
                        for _ in range(3):
                            safe_rmtree(target_dir, ignore_errors=True)
                            if not target_dir.exists():
                                break
                            time.sleep(0.2)
                    if target_dir.exists():
                        raise DownloadError(f'版本名称已存在：{name}\n请修改整合包名称后重试')
                self._created_target = True
            self._alias_id = name
            mode_text = '安装到现有实例' if self.existing_version else '导入整合包'
            self.status_updated.emit(f'正在{mode_text}：{name}')
            self.log_message.emit(f'格式: {info.format_name()}，MC {info.mc_version}，加载器: {info.loader_id or "原版"} {info.loader_version}')
            self.log_message.emit(f'文件数: {info.file_count}')
            self._before_dirs = register_active_install(self._install_token, versions_dir)
            alias_dir = target_dir
            alias_dir.mkdir(parents=True, exist_ok=True)
            (alias_dir / 'mods').mkdir(exist_ok=True)
            if self._created_target:
                (alias_dir / INCOMPLETE_MARKER).write_text('importing\n', encoding='utf-8')
                self._install_dependencies(info, name)
                # 安装器可能整体重建了目标目录，重新写入未完成标记
                (alias_dir / INCOMPLETE_MARKER).write_text('importing\n', encoding='utf-8')
                self._mark_dependency_dirs()
            else:
                self.status_updated.emit('使用现有实例，跳过加载器安装')
            self.progress_updated.emit(58)
            pack_files = self._resolve_files(info, alias_dir)
            self._download_files(info, pack_files, alias_dir)
            self._extract_overrides(info, alias_dir)
            self._verify_imported_mods(alias_dir, pack_files)
            icon_name = self._extract_pack_icon(info, alias_dir)
            if not icon_name and self.icon_url:
                icon_name = self._download_remote_icon(alias_dir)
            installed_ids = set()
            try:
                from core.mod_scan import scan_mod_ids
                installed_ids = scan_mod_ids(alias_dir / 'mods')
                pack_jar_count = sum(
                    1 for record in pack_files
                    if str(record.get('path') or '').lower().startswith('mods/')
                    and str(record.get('path') or '').lower().endswith(('.jar', '.litemod'))
                )
                self.log_message.emit(
                    f'整合包 Mod 校验: 清单 {pack_jar_count} 个文件，'
                    f'识别 {len(installed_ids)} 个 modid'
                )
            except Exception as exc:
                self.log_message.emit(f'整合包 Mod 校验失败: {exc}')
            meta = {
                'format': info.kind,
                'name': info.name,
                'version': info.version,
                'author': info.author,
                'description': info.summary,
                'mc_version': info.mc_version,
                'loader': info.loader_id,
                'loader_version': info.loader_version,
                'icon': icon_name,
                'imported_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'files': pack_files,
                'display_name': info.name or name,
                'launch_name': info.name or name,
                'mod_count': len(installed_ids),
                'install_mode': 'existing' if self.existing_version else 'new',
            }
            if self.existing_version and not self._backup_existing_file(alias_dir / 'pmgl_pack.json'):
                raise DownloadError('无法备份现有版本元数据，已中止安装')
            with open(alias_dir / 'pmgl_pack.json', 'w', encoding='utf-8') as fh:
                json.dump(meta, fh, ensure_ascii=False, indent=2)
            settings = self._config.get('version_settings', {})
            entry = settings.get(name, {})
            entry['isolated'] = True
            settings[name] = entry
            self._config.set('version_settings', settings)
            self._config.save()
            if self._created_target:
                try:
                    (alias_dir / INCOMPLETE_MARKER).unlink(missing_ok=True)
                except OSError:
                    pass
                self._clear_dependency_markers()
            self.progress_updated.emit(100)
            self.status_updated.emit('整合包导入完成')
            self.finished_ok.emit(name)
        except (InstallCancelled, DownloadCancelled):
            self._cleanup_failed(alias_dir)
            self.finished_error.emit('导入已取消')
        except Exception as exc:
            self._cleanup_failed(alias_dir)
            self.log_message.emit(f'导入失败: {exc}')
            self.finished_error.emit(str(exc))
        finally:
            unregister_active_install(self._install_token)

    def _cleanup_failed(self, alias_dir):
        # 安装到现有实例时绝不能删除整个版本目录；只允许回滚本次新建的实例。
        if self._created_target and alias_dir and alias_dir.exists():
            for _ in range(3):
                safe_rmtree(alias_dir, ignore_errors=True)
                if not alias_dir.exists():
                    break
                time.sleep(0.2)
        if self._created_target:
            self._remove_created_dependencies()

    def _created_dependency_dirs(self):
        if self.existing_version or self._before_dirs is None:
            return []
        versions_dir = Path(self.mc_dir) / 'versions'
        if not versions_dir.exists():
            return []
        return [item for item in versions_dir.iterdir() if item.is_dir() and item.name not in self._before_dirs and item.name != self._alias_id]

    def _remove_created_dependencies(self):
        for path in self._created_dependency_dirs():
            for _ in range(3):
                safe_rmtree(path, ignore_errors=True)
                if not path.exists():
                    break
                time.sleep(0.2)

    def _mark_dependency_dirs(self):
        for path in self._created_dependency_dirs():
            try:
                (path / INCOMPLETE_MARKER).write_text('dependency\n', encoding='utf-8')
            except OSError:
                pass

    def _clear_dependency_markers(self):
        for path in self._created_dependency_dirs():
            try:
                (path / INCOMPLETE_MARKER).unlink(missing_ok=True)
            except OSError:
                pass

    def _install_dependencies(self, info, alias_id):
        progress = ProgressMapper(5, 50, self.progress_updated.emit)
        common = dict(status_cb=self._throttled_status, progress_cb=progress, log_cb=self._throttled_log, cancel=(lambda: self._cancelled))
        if info.loader_id == 'fabric':
            install_fabric(info.mc_version, info.loader_version, self.mc_dir, alias_id, **common)
            return
        if info.loader_id == 'quilt':
            install_quilt(info.mc_version, info.loader_version, self.mc_dir, alias_id, **common)
            return
        if info.loader_id == 'forge':
            forge_version = info.loader_version
            prefix = f'{info.mc_version}-'
            if forge_version.startswith(prefix):
                forge_version = forge_version[len(prefix):]
            if not forge_version:
                raise DownloadError('整合包未指定 Forge 版本')
            install_forge(info.mc_version, forge_version, self.mc_dir, alias_id, **common)
            return
        if info.loader_id == 'neoforge':
            if not info.loader_version:
                raise DownloadError('整合包未指定 NeoForge 版本')
            install_neoforge(info.mc_version, info.loader_version, self.mc_dir, alias_id, **common)
            return
        install_vanilla(info.mc_version, self.mc_dir, status_cb=self._throttled_status, progress_cb=ProgressMapper(5, 50, self.progress_updated.emit), log_cb=self._throttled_log, cancel=(lambda: self._cancelled))
        create_alias(self.mc_dir, alias_id, info.mc_version, jar_id=info.mc_version)
        self.progress_updated.emit(55)

    def _resolve_files(self, info, alias_dir):
        '''把清单里的文件解析成可下载条目，并记录到 pmgl_pack.json。'''

        records = []
        if not info.files:
            return records
        pending_curse = [item for item in info.files if item.project_id and item.file_id and not item.urls]
        resolved = {}
        if pending_curse:
            self.status_updated.emit(f'正在解析 {len(pending_curse)} 个 CurseForge 文件...')
            total = len(pending_curse)
            done = 0
            last_progress = [0.0]

            def resolve(item):
                source = CurseForgeSource()
                try:
                    return item, source.get_file_by_id(item.project_id, item.file_id, mc_version=info.mc_version, loader=info.loader_id)
                finally:
                    try:
                        source.http.close()
                    except Exception:
                        pass

            with ThreadPoolExecutor(max_workers=max(1, min(4, total, os.cpu_count() or 4))) as pool:
                future_map = {pool.submit(resolve, item): item for item in pending_curse}
                for future in as_completed(future_map):
                    self._check_cancel()
                    item = future_map[future]
                    try:
                        _, mod_file = future.result()
                    except Exception:
                        mod_file = None
                    if mod_file is None:
                        if item.required and not item.file_name:
                            raise DownloadError(f'无法获取 CurseForge 文件 {item.project_id}/{item.file_id}。可尝试在设置中填写 CurseForge API Key。')
                        if not item.file_name:
                            self.log_message.emit(f'跳过可选 CurseForge 文件 {item.project_id}/{item.file_id}')
                    else:
                        resolved[(item.project_id, item.file_id)] = mod_file
                    done += 1
                    now = time.monotonic()
                    if done == total or now - last_progress[0] >= 0.25:
                        last_progress[0] = now
                        self.progress_updated.emit(55 + int(5 * done / total))
        for item in info.files:
            if item.env_client.lower() == 'unsupported':
                continue
            rel = item.path
            urls = list(item.urls)
            file_name = item.file_name
            sha1 = item.sha1
            size = item.size
            if item.project_id and item.file_id:
                mod_file = resolved.get((item.project_id, item.file_id))
                if mod_file is not None:
                    file_name = mod_file.filename
                    sha1 = mod_file.sha1 or sha1
                    size = mod_file.size or size
                    if not urls and mod_file.download_url:
                        urls = [mod_file.download_url]
                if not file_name:
                    if item.required:
                        raise DownloadError(f'CurseForge 文件 {item.project_id}/{item.file_id} 缺少文件名')
                    continue
                try:
                    fid = int(item.file_id)
                except (TypeError, ValueError):
                    fid = 0
                if not urls and fid and file_name:
                    urls = [f'https://edge.forgecdn.net/files/{fid // 1000}/{fid % 1000}/{quote(file_name)}']
                if fid:
                    mirror = f'https://mod.mcimirror.top/files/{fid // 1000}/{fid % 1000}/{quote(file_name)}'
                    if mirror not in urls:
                        urls.append(mirror)
                rel = str(file_name or '').replace('\\', '/')
                if not rel.lower().startswith('mods/'):
                    rel = f'mods/{Path(rel).name}'
                else:
                    rel = 'mods/' + Path(rel).name
            if not rel or not urls:
                if item.required:
                    raise DownloadError(f'整合包文件 {rel or file_name or "未知文件"} 没有可用的下载地址')
                continue
            expanded = []
            for url in urls:
                for candidate in _forgecdn_candidates(url):
                    if candidate not in expanded:
                        expanded.append(candidate)
            root = 'mc' if rel.replace('\\', '/').lower().startswith(('libraries/', 'assets/')) else 'game'
            try:
                _safe_join(self.mc_dir if root == 'mc' else alias_dir, rel)
            except DownloadError:
                if item.required:
                    raise DownloadError(f'整合包文件路径非法，已中止导入: {rel}')
                self.log_message.emit(f'跳过可选非法路径: {rel}')
                continue
            records.append({
                'path': rel.replace('\\', '/'),
                'root': root,
                'urls': expanded,
                'sha1': sha1,
                'size': size,
                'required': bool(item.required),
                'env': {'client': item.env_client or 'required'},
            })
        return records

    def _backup_existing_file(self, dest):
        dest = Path(dest)
        if not dest.is_file():
            return True
        try:
            backup = dest.with_name(
                f'{dest.name}.bak-{time.strftime("%Y%m%d-%H%M%S")}'
                f'-{uuid.uuid4().hex[:8]}'
            )
            shutil.copy2(dest, backup)
            self.log_message.emit(f'已备份现有文件: {dest.name} -> {backup.name}')
            return True
        except OSError as exc:
            self.log_message.emit(f'备份现有文件失败: {dest.name} ({exc})')
            return False

    def _download_files(self, info, records, alias_dir):
        if not records:
            return
        known_bytes = sum(max(0, int(record.get('size') or 0)) for record in records)
        if known_bytes:
            reserve_bytes = int(known_bytes * 1.10)
            free_candidates = []
            for root in {Path(alias_dir), Path(self.mc_dir)}:
                try:
                    free_candidates.append(shutil.disk_usage(str(root)).free)
                except OSError:
                    continue
            if free_candidates and reserve_bytes > min(free_candidates):
                free_bytes = min(free_candidates)
                raise DownloadError(
                    '磁盘空间不足：需要约 %.2f GB，当前剩余 %.2f GB'
                    % (
                        max(0.01, reserve_bytes / (1024 ** 3)),
                        max(0.01, free_bytes / (1024 ** 3)),
                    )
                )
        downloader = DownloadClient(self.mc_dir, fast=False, file_deadline=self._file_deadline)
        remotes = []
        for record in records:
            base = Path(self.mc_dir) if record['root'] == 'mc' else alias_dir
            try:
                dest = _safe_join(base, record['path'])
            except DownloadError:
                continue
            if (
                self.existing_version
                and str(base) != str(self.mc_dir)
                and not self._backup_existing_file(dest)
            ):
                raise DownloadError(f'无法备份现有文件，已中止安装: {dest}')
            remotes.append(RemoteFile(
                label=f'整合包文件 {record["path"]}',
                dest=dest, urls=record['urls'], sha1=record['sha1'] or '',
                size=record['size'], optional=not record['required'],
                category='pack', check_zip=record['path'].lower().endswith(('.jar', '.zip')),
            ))
        total = max(1, len(remotes))
        self.status_updated.emit(f'正在下载整合包内容（{total} 个文件）...')
        state = {'done': 0, 'bytes': 0}
        state_lock = threading.Lock()
        last = [0.0, 0.0, time.time()]
        last_status = [0.0]

        def report(force=False):
            now = time.time()
            with state_lock:
                if not force and now - last[0] < 0.25:
                    return
                last[0] = now
                percent = 58 + int(30 * min(1.0, state['done'] / total))
                if now - last[2] >= 0.5:
                    speed = (state['bytes'] - last[1]) / max(0.001, now - last[2])
                    last[1] = state['bytes']
                    last[2] = now
                    speed_text = _format_speed(speed)
                else:
                    speed_text = ''
            self.progress_updated.emit(percent)
            if speed_text:
                self.stats_updated.emit(speed_text, str(max(0, total - state['done'])))

        def on_progress(remote, current, file_total):
            report(False)

        def on_file_done(remote, ok, changed, error):
            now = time.monotonic()
            with state_lock:
                state['done'] += 1
                state['bytes'] += int(remote.size or (remote.dest.stat().st_size if remote.dest.exists() else 0))
                done = state['done']
            if ok and (done == total or now - last_status[0] >= 0.4):
                last_status[0] = now
                self.status_updated.emit(f'已完成 {remote.label}（{done}/{total}）')
            elif not ok and remote.optional:
                self.log_message.emit(f'可选文件下载失败: {remote.label} ({error})')
            elif not ok:
                self.log_message.emit(f'下载失败: {remote.label} ({error})')
            report(False)

        try:
            worker_count = min(6, max(2, os.cpu_count() or 4), DownloadGate.max_threads(), total)
            result = downloader.download_many(remotes, workers=worker_count, cancel=(lambda: self._cancelled), on_file_done=on_file_done, on_progress=on_progress)
            if result.failed:
                # 并发下载时个别大文件可能因连接抖动失败；再串行重试一次，
                # 避免整个整合包因为少数文件而回滚。
                failed_labels = set(result.failed_items)
                retry_files = [remote for remote in remotes if remote.label in failed_labels]
                if retry_files:
                    self.status_updated.emit(f'正在重试 {len(retry_files)} 个下载失败的文件...')
                    for remote in retry_files:
                        self._check_cancel()
                        try:
                            downloader.download(remote, cancel=(lambda: self._cancelled))
                        except (InstallCancelled, DownloadCancelled):
                            raise
                        except Exception as exc:
                            self.log_message.emit(f'重试失败: {remote.label} ({exc})')
                    still_failed = [remote for remote in remotes if not remote.optional and not remote.valid('full')]
                    result.failed = len(still_failed)
                    result.failed_items = [remote.label for remote in still_failed[:10]]
        finally:
            downloader.close()
        report(True)
        if result.failed:
            raise DownloadError('整合包文件下载不完整：' + '、'.join(result.failed_items[:6]))

    def _extract_overrides(self, info, alias_dir):
        prefixes = []
        for prefix in info.overrides:
            value = str(prefix or '').replace('\\', '/')
            if value and not value.endswith('/'):
                value += '/'
            if value and value not in prefixes:
                prefixes.append(value)
        if not prefixes:
            return
        self.status_updated.emit('正在解压整合包覆盖文件...')
        with zipfile.ZipFile(self.path) as zf:
            actual = {name.replace('\\', '/'): name for name in zf.namelist()}
            entries = list(actual)
            selected = [name for name in entries if not name.endswith('/') and any(name.startswith(prefix) for prefix in prefixes)]
            selected_infos = [zf.getinfo(actual[name]) for name in selected]
            total = max(1, len(selected))
            # 与 PCL2/HMCL 一致：不设置固定解压大小上限，只检查磁盘余量。
            required_bytes = sum(max(0, int(item.file_size or 0)) for item in selected_infos)
            if required_bytes:
                reserve_bytes = int(required_bytes * 1.10)
                free_candidates = []
                for root in {Path(alias_dir), Path(self.mc_dir)}:
                    try:
                        free_candidates.append(shutil.disk_usage(str(root)).free)
                    except OSError:
                        continue
                if free_candidates and reserve_bytes > min(free_candidates):
                    free_bytes = min(free_candidates)
                    raise DownloadError(
                        '磁盘空间不足：需要约 %.2f GB，当前剩余 %.2f GB'
                        % (
                            max(0.01, reserve_bytes / (1024 ** 3)),
                            max(0.01, free_bytes / (1024 ** 3)),
                        )
                    )
            for index, name in enumerate(selected, 1):
                self._check_cancel()
                prefix = next((p for p in prefixes if name.startswith(p)), '')
                rel = name[len(prefix):] if prefix else name
                if not rel:
                    continue
                base = self.mc_dir if rel.lower().startswith(('libraries/', 'assets/')) else alias_dir
                dest = _safe_join(base, rel)
                if (
                    self.existing_version
                    and str(base) != str(self.mc_dir)
                    and not self._backup_existing_file(dest)
                ):
                    raise DownloadError(f'无法备份现有文件，已中止安装: {dest}')
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(actual[name]) as src, open(dest, 'wb') as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                if index % 20 == 0 or index == total:
                    self.progress_updated.emit(88 + int(6 * index / total))
                    self.status_updated.emit(f'正在解压覆盖文件 {index}/{total}')

    _PACK_ICON_CANDIDATES = (
        'icon.png', 'icon.jpg', 'icon.jpeg', 'icon.webp',
        'pack.png', 'pack.jpg', 'pack.jpeg',
        'logo.png', 'logo.jpg', 'logo.jpeg',
        'thumbnail.png', 'thumbnail.jpg', 'loading/logo.png',
    )
    @staticmethod
    def _is_image_bytes(data):
        if not data or len(data) > MAX_ICON_BYTES:
            return False
        return data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8') or data.startswith((b'GIF87a', b'GIF89a', b'BM')) or (data[:4] == b'RIFF' and data[8:12] == b'WEBP')

    def _extract_pack_icon(self, info, alias_dir):
        try:
            with zipfile.ZipFile(self.path) as zf:
                actual = {name.replace('\\', '/').lower(): name for name in zf.namelist()}
                prefixes = [p.replace('\\', '/').lower() for p in info.icon_prefixes or ['']]
                icon_key = str(info.extra.get('icon_key') or '').strip()
                candidates = list(self._PACK_ICON_CANDIDATES)
                if icon_key:
                    key = icon_key.replace('\\', '/').strip('/')
                    extra = []
                    if re.search(r'\.(?:png|jpe?g|webp|gif|bmp)$', key, re.I):
                        extra.append(key)
                    else:
                        extra.extend(
                            key + ext
                            for ext in (
                                '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'
                            )
                        )
                    candidates = extra + candidates
                for prefix in prefixes:
                    for candidate in candidates:
                        name = actual.get(prefix + candidate)
                        if not name:
                            continue
                        try:
                            info = zf.getinfo(name)
                            if int(info.file_size or 0) > MAX_ICON_BYTES:
                                continue
                            data = zf.read(name)
                        except (OSError, KeyError):
                            continue
                        if not self._is_image_bytes(data):
                            continue
                        if self.existing_version and not self._backup_existing_file(alias_dir / 'icon.png'):
                            raise DownloadError('无法备份现有图标，已中止安装')
                        (alias_dir / 'icon.png').write_bytes(data)
                        self.log_message.emit(f'已提取整合包图标: {name}')
                        return 'icon.png'
        except (OSError, zipfile.BadZipFile):
            return ''
        return ''

    def _verify_imported_mods(self, alias_dir, records):
        '''统计实际/应安装 Mod；required 缺失时禁止报导入完成。'''

        mods_dir = Path(alias_dir) / 'mods'
        required = []
        for record in records or ():
            rel = str(record.get('path') or '').replace('\\', '/')
            if not rel.lower().startswith('mods/'):
                continue
            if not rel.lower().endswith(('.jar', '.litemod')):
                continue
            if not record.get('required', True):
                continue
            required.append(record)
        missing = []
        for record in required:
            try:
                dest = _safe_join(Path(alias_dir), record['path'])
            except DownloadError:
                missing.append(record['path'])
                continue
            if not dest.is_file() or dest.stat().st_size <= 0:
                missing.append(record['path'])
        actual_files = 0
        if mods_dir.is_dir():
            for path in mods_dir.rglob('*'):
                if (
                    path.is_file()
                    and path.suffix.lower() in ('.jar', '.litemod')
                ):
                    actual_files += 1
        self.log_message.emit(
            f'Mod 完整性: 实际 {actual_files} 个文件，'
            f'清单 required {len(required)} 个文件'
        )
        if missing:
            sample = '、'.join(missing[:5])
            raise DownloadError(
                f'整合包导入不完整，缺少 {len(missing)} 个必需 Mod 文件：'
                f'{sample}'
            )

    def _download_remote_icon(self, alias_dir):
        '''包内没有图标时，用资源中心封面补齐；失败不阻断导入。'''

        if not self.icon_url:
            return ''
        client = None
        try:
            client = HttpClient(timeout=20, retries=1)
            response = client.get(self.icon_url)
            response.raise_for_status()
            data = bytes(response.content)
            if not self._is_image_bytes(data):
                self.log_message.emit('资源中心封面不是有效图片，已跳过')
                return ''
            if self.existing_version and not self._backup_existing_file(
                alias_dir / 'icon.png'
            ):
                raise DownloadError('无法备份现有图标，已中止安装')
            (alias_dir / 'icon.png').write_bytes(data)
            self.log_message.emit('已下载资源中心整合包封面')
            return 'icon.png'
        except DownloadError:
            raise
        except Exception as exc:
            self.log_message.emit(f'下载整合包封面失败，继续导入: {exc}')
            return ''
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
