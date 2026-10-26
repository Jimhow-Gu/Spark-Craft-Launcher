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

import json
import os
import re
import shutil
import threading
import time
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
from core.mods import CurseForgeSource
from core.net import (DownloadCancelled, DownloadError, DownloadGate)
from core.version import (INCOMPLETE_MARKER, register_active_install, unregister_active_install)


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
        return {'mrpack': 'Modrinth', 'curseforge': 'CurseForge', 'multimc': 'MultiMC/Prism', 'hmcl': 'HMCL/MCBBS'}.get(self.kind, self.kind)


def _read_json(zf, name):
    return json.loads(zf.read(name).decode('utf-8-sig'))


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


def sanitize_version_name(name):
    name = (name or '').strip()
    name = re.sub('[<>:"/\\\\|?*\\x00-\\x1f]', '-', name)
    name = re.sub('\\s+', ' ', name).strip(' .')
    return name[:80]


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
    if 'forgecdn.net' not in url:
        return [url]
    path = url.split('forgecdn.net', 1)[1]
    result = []
    for host in ('https://mediafilez.forgecdn.net', 'https://edge.forgecdn.net'):
        candidate = host + path
        if candidate not in result:
            result.append(candidate)
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
        # Modrinth
        entry = _find_entry(normalized, 'modrinth.index.json', root_only=False)
        if entry:
            return _inspect_modrinth(path, zf, entry, normalized)
        # MultiMC / Prism
        entry = _find_entry(normalized, 'mmc-pack.json', root_only=False)
        if entry and _find_entry(normalized, 'instance.cfg', root_only=False):
            return _inspect_multimc(path, zf, entry, normalized)
        # CurseForge / HMCL / MCBBS
        entry = _find_entry(normalized, 'manifest.json', root_only=False)
        if entry:
            return _inspect_manifest_pack(path, zf, entry, normalized)
    raise DownloadError('无法识别的整合包格式（支持 Modrinth、CurseForge/FTB、MultiMC/Prism、HMCL/MCBBS 导出包）')


def _pack_root(entry):
    return entry.rsplit('/', 1)[0] + '/' if '/' in entry else ''


def _inspect_modrinth(path, zf, entry, names):
    index = _read_json(zf, entry)
    deps = index.get('dependencies') or {}
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
        summary=str(index.get('summary') or ''), file_count=len(files),
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
    instance_entry = _find_entry(names, 'instance.cfg', root_only=False)
    if instance_entry:
        try:
            text = zf.read(instance_entry).decode('utf-8', 'replace')
            match = re.search(r'^name=(.+)$', text, re.M)
            if match:
                name = match.group(1).strip()
        except (OSError, KeyError):
            pass
    prefixes = [root + '.minecraft/', root + 'minecraft/']
    return ModpackInfo(
        path=path, kind='multimc', name=name, version='',
        mc_version=mc_version, loader_id=loader_id, loader_version=loader_version,
        summary='MultiMC / Prism 实例', file_count=_count_entries(names, prefixes),
        overrides=prefixes, icon_prefixes=[root, root + '.minecraft/', root + 'minecraft/'],
    )


def _inspect_manifest_pack(path, zf, entry, names):
    manifest = _read_json(zf, entry)
    if not isinstance(manifest, dict):
        raise DownloadError('manifest.json 内容无效')
    files_raw = [item for item in manifest.get('files') or [] if isinstance(item, dict)]
    minecraft = manifest.get('minecraft') if isinstance(manifest.get('minecraft'), dict) else {}
    is_curse = bool(minecraft) or any('projectID' in item for item in files_raw)
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
            summary=str(manifest.get('author') or ''), file_count=len(files),
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
    mc_version = str(manifest.get('gameVersion') or manifest.get('mcVersion') or '')
    loader_id, loader_version = '', ''
    overrides = str(manifest.get('overrides') or 'overrides')
    prefixes = [overrides.rstrip('/') + '/'] if overrides else []
    if 'overrides/' not in prefixes:
        prefixes.append('overrides/')
    return ModpackInfo(
        path=path, kind='hmcl', name=str(manifest.get('name') or path.stem),
        version=str(manifest.get('version') or ''), mc_version=mc_version,
        loader_id=loader_id, loader_version=loader_version,
        summary=str(manifest.get('description') or manifest.get('author') or ''),
        file_count=len(files), author=str(manifest.get('author') or ''),
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

    def __init__(self, path, mc_dir, pack_name='', parent=None):
        super().__init__(parent)
        self.path = str(path)
        self.mc_dir = str(Path(mc_dir))
        self.pack_name = pack_name
        self._cancelled = False
        self._file_deadline = 90
        self._alias_id = ''
        self._config = ConfigManager()
        self._before_dirs = None
        self._install_token = f'modpack-{id(self)}-{int(time.time() * 1000)}'

    def cancel(self):
        self._cancelled = True

    def _check_cancel(self):
        if self._cancelled:
            raise InstallCancelled()

    def run(self):
        alias_dir = None
        try:
            info = inspect_modpack(self.path)
            name = sanitize_version_name(self.pack_name or info.name or Path(self.path).stem) or '整合包'
            if not info.mc_version:
                raise DownloadError('整合包未包含 Minecraft 版本信息，无法自动安装。请手动创建版本后导入文件。')
            versions_dir = Path(self.mc_dir) / 'versions'
            versions_dir.mkdir(parents=True, exist_ok=True)
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
            self._alias_id = name
            self.status_updated.emit(f'正在导入整合包：{name}')
            self.log_message.emit(f'格式: {info.format_name()}，MC {info.mc_version}，加载器: {info.loader_id or "原版"} {info.loader_version}')
            self.log_message.emit(f'文件数: {info.file_count}')
            self._before_dirs = register_active_install(self._install_token, versions_dir)
            alias_dir = target_dir
            alias_dir.mkdir(parents=True, exist_ok=True)
            (alias_dir / INCOMPLETE_MARKER).write_text('importing\n', encoding='utf-8')
            (alias_dir / 'mods').mkdir(exist_ok=True)
            self._install_dependencies(info, name)
            # 安装器可能整体重建了目标目录，重新写入未完成标记
            (alias_dir / INCOMPLETE_MARKER).write_text('importing\n', encoding='utf-8')
            self._mark_dependency_dirs()
            self.progress_updated.emit(58)
            pack_files = self._resolve_files(info, alias_dir)
            self._download_files(info, pack_files, alias_dir)
            self._extract_overrides(info, alias_dir)
            icon_name = self._extract_pack_icon(info, alias_dir)
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
            }
            with open(alias_dir / 'pmgl_pack.json', 'w', encoding='utf-8') as fh:
                json.dump(meta, fh, ensure_ascii=False, indent=2)
            settings = self._config.get('version_settings', {})
            entry = settings.get(name, {})
            entry['isolated'] = True
            settings[name] = entry
            self._config.set('version_settings', settings)
            self._config.save()
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
        if alias_dir and alias_dir.exists():
            for _ in range(3):
                safe_rmtree(alias_dir, ignore_errors=True)
                if not alias_dir.exists():
                    break
                time.sleep(0.2)
        self._remove_created_dependencies()

    def _created_dependency_dirs(self):
        if self._before_dirs is None:
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
        common = dict(status_cb=self.status_updated.emit, progress_cb=progress, log_cb=self.log_message.emit, cancel=(lambda: self._cancelled))
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
        install_vanilla(info.mc_version, self.mc_dir, status_cb=self.status_updated.emit, progress_cb=ProgressMapper(5, 50, self.progress_updated.emit), log_cb=self.log_message.emit, cancel=(lambda: self._cancelled))
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

            def resolve(item):
                source = CurseForgeSource()
                try:
                    return item, source.get_file_by_id(item.project_id, item.file_id, mc_version=info.mc_version, loader=info.loader_id)
                finally:
                    try:
                        source.http.close()
                    except Exception:
                        pass

            with ThreadPoolExecutor(max_workers=max(1, min(8, total))) as pool:
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
                rel = f'mods/{file_name}'
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
                self.log_message.emit(f'跳过非法路径: {rel}')
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

    def _download_files(self, info, records, alias_dir):
        if not records:
            return
        downloader = DownloadClient(self.mc_dir, fast=False, file_deadline=self._file_deadline)
        remotes = []
        for record in records:
            base = Path(self.mc_dir) if record['root'] == 'mc' else alias_dir
            try:
                dest = _safe_join(base, record['path'])
            except DownloadError:
                continue
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
            with state_lock:
                state['done'] += 1
                state['bytes'] += int(remote.size or (remote.dest.stat().st_size if remote.dest.exists() else 0))
            if ok:
                self.status_updated.emit(f'已完成 {remote.label}（{state["done"]}/{total}）')
            elif remote.optional:
                self.log_message.emit(f'可选文件下载失败: {remote.label} ({error})')
            report(False)

        try:
            result = downloader.download_many(remotes, workers=min(8, DownloadGate.max_threads(), total), cancel=(lambda: self._cancelled), on_file_done=on_file_done, on_progress=on_progress)
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
            entries = [name.replace('\\', '/') for name in zf.namelist()]
            selected = [name for name in entries if not name.endswith('/') and any(name.startswith(prefix) for prefix in prefixes)]
            total = max(1, len(selected))
            for index, name in enumerate(selected, 1):
                self._check_cancel()
                prefix = next((p for p in prefixes if name.startswith(p)), '')
                rel = name[len(prefix):] if prefix else name
                if not rel:
                    continue
                base = self.mc_dir if rel.lower().startswith(('libraries/', 'assets/')) else alias_dir
                dest = _safe_join(base, rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(dest, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
                if index % 20 == 0 or index == total:
                    self.progress_updated.emit(88 + int(6 * index / total))
                    self.status_updated.emit(f'正在解压覆盖文件 {index}/{total}')

    _PACK_ICON_CANDIDATES = ('icon.png', 'pack.png', 'logo.png', 'loading/logo.png')
    @staticmethod
    def _is_image_bytes(data):
        if not data or len(data) > 8388608:
            return False
        return data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8') or data.startswith((b'GIF87a', b'GIF89a', b'BM')) or (data[:4] == b'RIFF' and data[8:12] == b'WEBP')

    def _extract_pack_icon(self, info, alias_dir):
        try:
            with zipfile.ZipFile(self.path) as zf:
                actual = {name.replace('\\', '/').lower(): name for name in zf.namelist()}
                prefixes = [p.replace('\\', '/').lower() for p in info.icon_prefixes or ['']]
                for prefix in prefixes:
                    for candidate in self._PACK_ICON_CANDIDATES:
                        name = actual.get(prefix + candidate)
                        if not name:
                            continue
                        try:
                            data = zf.read(name)
                        except (OSError, KeyError):
                            continue
                        if not self._is_image_bytes(data):
                            continue
                        (alias_dir / 'icon.png').write_bytes(data)
                        self.log_message.emit(f'已提取整合包图标: {name}')
                        return 'icon.png'
        except (OSError, zipfile.BadZipFile):
            return ''
        return ''
