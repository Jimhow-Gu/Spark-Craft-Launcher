'''
游戏文件修复 - 校验版本 JSON、客户端 JAR、依赖库、资源文件、整合包文件与 natives，
自动补全缺失/损坏文件。下载源与候选顺序由 core.downloading 统一提供。
'''

from __future__ import annotations
import json
import logging
import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import List
from PySide6.QtCore import QThread, Signal
from core.config import ConfigManager
from core.downloading import (DownloadClient, RemoteFile, asset_index_urls, asset_object_urls, client_urls, fetch_version_json, forge_companion_urls, forge_universal_urls, library_applies as _library_applies, library_urls, log_config_urls, maven_path as _maven_path, rewrite_mojang_url as _rewrite_mojang_url)
from core.net import DownloadCancelled, DownloadError, DownloadGate
try:
    from minecraft_launcher_lib.install import get_natives
except Exception:
    def get_natives(_lib):
        return ''

FileTask = RemoteFile
logger = logging.getLogger('Repair')

@dataclass
class RepairResult:
    checked: 'int' = 0
    fixed: 'int' = 0
    failed: 'int' = 0
    failed_items: 'List[str]' = field(default_factory=list)
    warnings: 'List[str]' = field(default_factory=list)
    def summary(self):
        text = f'修复完成：检查 {self.checked} 个文件，补全/修复 {self.fixed} 个'
        if self.failed:
            text += f'，失败 {self.failed} 个'
        return text


class RepairCancelled(DownloadCancelled):
    '''取消修复；与下载取消同属一类，便于各安装 Worker 统一清理。'''

class RepairEngine:
    def __init__(self, version_id, mc_dir, *, status=None, progress=None, phase=None, log=None, cancel=None, fast=False, missing_only=False):
        self.requested_id = version_id
        self.mc_dir = Path(mc_dir)
        self.versions_dir = self.mc_dir / 'versions'
        self.fast = bool(fast)
        self.missing_only = bool(missing_only)
        self.status = status or (lambda _s: None)
        self.progress = progress or (lambda _p: None)
        self._progress_last = 0.0
        self.phase = phase or (lambda _k, _p: None)
        self.log = log or (lambda _s: None)
        self.cancel = cancel or (lambda: False)
        self._file_source = str(ConfigManager().get('file_download_source', 'auto') or 'auto')
        self.downloader = DownloadClient(self.mc_dir, source=self._file_source, fast=self.fast, missing_only=self.missing_only)
        self.http = self.downloader.client()
        self.result = RepairResult()
        self._virtual_assets = []

    def _resolve_version(self):
        version_id = self.requested_id
        path = self.versions_dir / version_id
        if path.is_dir() and (path / f'{version_id}.json').exists():
            return version_id, path
        if self.versions_dir.exists():
            for item in self.versions_dir.iterdir():
                if item.is_dir() and item.name.lower().startswith(version_id.lower()):
                    json_files = list(item.glob('*.json'))
                    if json_files:
                        return item.name, item
        path.mkdir(parents=True, exist_ok=True)
        return version_id, path

    def _fetch_version_json(self, version_id, dest):
        self.status(f'正在获取版本 {version_id} 的元数据...')
        data = fetch_version_json(version_id, http=self.http, source=self._file_source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, dest)
        self.log(f'已下载版本元数据: {dest.name}')
        return data

    def _read_version_json(self, version_id, version_dir):
        path = version_dir / f'{version_id}.json'
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and data.get('id'):
                    return data
            except Exception:
                self.log(f'版本 JSON 损坏，重新下载: {path}')
        return self._fetch_version_json(version_id, path)

    def _load_chain(self):
        version_id, version_dir = self._resolve_version()
        chain = []
        seen = set()
        current_id, current_dir = version_id, version_dir
        while current_id and current_id not in seen and len(chain) < 8:
            seen.add(current_id)
            data = self._read_version_json(current_id, current_dir)
            data['id'] = data.get('id') or current_id
            chain.append((current_id, current_dir, data))
            parent = data.get('inheritsFrom')
            if not parent:
                break
            parent_dir = self.versions_dir / parent
            parent_dir.mkdir(parents=True, exist_ok=True)
            current_id, current_dir = parent, parent_dir
        if not chain:
            raise DownloadError(f'版本 {version_id} 的 JSON 无法读取')
        self._ensure_version_jars(chain)
        return chain

    def _ensure_version_jars(self, chain):
        '''修复版本目录内 JAR 文件名与版本 ID 不一致的问题。'''

        for dir_id, ver_dir, data in chain:
            if data.get('jar'):
                continue
            target = ver_dir / f'{dir_id}.jar'
            if target.exists():
                continue
            jars = [p for p in ver_dir.glob('*.jar') if p.is_file()]
            if jars:
                try:
                    jars[0].rename(target)
                    self.log(f'已修正版本 JAR 文件名: {jars[0].name} -> {target.name}')
                except OSError:
                    pass
        parent_id = data.get('inheritsFrom')
        if parent_id:
            parent_jar = self.versions_dir / parent_id / f'{parent_id}.jar'
            if parent_jar.exists():
                try:
                    shutil.copy2(parent_jar, target)
                    self.log(f'已从父版本复制 JAR: {parent_id} -> {dir_id}')
                except OSError:
                    pass

    def _collect_tasks(self, chain):
        tasks = []
        seen_paths = set()
        def add(task):
            key = str(task.dest.resolve()).lower()
            if key in seen_paths:
                return
            seen_paths.add(key)
            tasks.append(task)

        libraries = {}
        asset_index = None
        logging_cfg = None
        for dir_id, ver_dir, data in reversed(chain):
            for lib in data.get('libraries', []) or []:
                if not (isinstance(lib, dict) and lib.get('name')):
                    continue
                if not _library_applies(lib):
                    continue
                name = str(lib['name'])
                parts = name.split('@', 1)[0].split(':')
                key = ':'.join(parts[:2])
                if len(parts) > 3:
                    key += ':' + parts[3]
                libraries[key] = lib
            if data.get('assetIndex'):
                asset_index = data['assetIndex']
            if data.get('logging', {}).get('client'):
                logging_cfg = data['logging']['client']
        for dir_id, ver_dir, data in chain:
            downloads = data.get('downloads') or {}
            client = downloads.get('client')
            if not client or not client.get('url'):
                continue
            add(FileTask(
                label=f'客户端 JAR ({dir_id})',
                urls=client_urls(dir_id, downloads, self._file_source),
                dest=ver_dir / f'{dir_id}.jar',
                sha1=str(client.get('sha1') or ''), size=client.get('size'),
                category='libraries', check_zip=True,
            ))
        for key, lib in libraries.items():
            downloads = lib.get('downloads') or {}
            artifact = downloads.get('artifact') or {}
            label = f'依赖库 {lib.get("name")}'
            path = str(artifact.get('path') or '')
            if not path and not lib.get('natives'):
                path = _maven_path(lib.get('name', ''))
            if path:
                add(FileTask(
                    label=label,
                    urls=library_urls(lib, source=self._file_source),
                    dest=self.mc_dir / 'libraries' / path,
                    sha1=str(artifact.get('sha1') or ''), size=artifact.get('size'),
                    category='libraries', check_zip=True,
                ))
            native = get_natives(lib)
            if not native:
                continue
            classifier = (downloads.get('classifiers') or {}).get(native) or {}
            native_path = str(classifier.get('path') or '') or _maven_path(lib.get('name', ''), native)
            if not native_path:
                continue
            add(FileTask(
                label=f'本地库 {lib.get("name")} ({native})',
                urls=library_urls(lib, native, self._file_source),
                dest=self.mc_dir / 'libraries' / native_path,
                sha1=str(classifier.get('sha1') or ''), size=classifier.get('size'),
                category='libraries', check_zip=True,
            ))
        self._add_forge_modules(add, libraries)
        if asset_index:
            index_id = asset_index.get('id') or 'legacy'
            index_path = self.mc_dir / 'assets' / 'indexes' / f'{index_id}.json'
            index_task = FileTask(
                label=f'资源索引 {index_id}',
                urls=asset_index_urls(asset_index, self._file_source),
                dest=index_path, sha1=str(asset_index.get('sha1') or ''),
                size=asset_index.get('size'), category='assets',
            )
            add(index_task)
            index_data = self._read_asset_index(index_path, index_task)
            if index_data:
                for name, obj in index_data.get('objects', {}).items():
                    if isinstance(obj, dict):
                        if not obj.get('hash'):
                            continue
                    h = obj['hash']
                    add(FileTask(
                        label=f'资源 {name}',
                        urls=asset_object_urls(h, self._file_source),
                        dest=self.mc_dir / 'assets' / 'objects' / h[:2] / h,
                        sha1=h, size=obj.get('size'), category='assets',
                    ))
                virtual = index_data.get('virtual')
                if virtual is True:
                    self._virtual_assets.append((index_data, 'legacy'))
                elif isinstance(virtual, str) and virtual:
                    self._virtual_assets.append((index_data, virtual))
                elif index_data.get('map_to_resources'):
                    self._virtual_assets.append((index_data, 'legacy'))
        if logging_cfg and logging_cfg.get('file'):
            file_info = logging_cfg['file']
            add(FileTask(
                label='日志配置',
                urls=log_config_urls(file_info, self._file_source),
                dest=self.mc_dir / 'assets' / 'log_configs' / file_info.get('id', 'log4j.xml'),
                sha1=str(file_info.get('sha1') or ''), size=file_info.get('size'),
                optional=True,
            ))
        self._add_pack_files(add, chain[0][1])
        return tasks, libraries_list(libraries)

    def _add_pack_files(self, add, version_dir):
        '''补全整合包自身文件（mods/config/资源包等），与 HMCL 的 completion task 对应。'''

        meta_path = version_dir / 'pmgl_pack.json'
        if not meta_path.is_file():
            return
        try:
            with open(meta_path, 'r', encoding='utf-8') as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            return
        for item in meta.get('files') or []:
            if not isinstance(item, dict):
                continue
            rel = str(item.get('path') or '').replace('\\', '/').strip('/')
            if not rel:
                continue
            rel_parts = PurePosixPath(rel).parts
            if not rel_parts or '..' in rel_parts:
                continue
            if (item.get('env') or {}).get('client') == 'unsupported':
                continue
            root = str(item.get('root') or 'game')
            base = self.mc_dir if root == 'mc' else version_dir
            dest = base / Path(*rel_parts)
            add(FileTask(
                label=f'整合包文件 {rel}',
                urls=[url for url in item.get('urls') or [] if isinstance(url, str)],
                dest=dest,
                sha1=str(item.get('sha1') or ''), size=item.get('size'),
                optional=not bool(item.get('required', True)),
                category='pack', check_zip=rel.lower().endswith(('.jar', '.zip')),
            ))

    def _read_asset_index(self, path, task):
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and 'objects' in data:
                    return data
            except Exception:
                pass
        for url in task.urls:
            try:
                data = self.http.get_json(url)
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, 'w', encoding='utf-8') as fh:
                    json.dump(data, fh, ensure_ascii=False)
                return data
            except Exception:
                pass
        return None

    def _extract_virtual_assets(self, index_data, virtual_name):
        objects_dir = self.mc_dir / 'assets' / 'objects'
        for name, obj in index_data.get('objects', {}).items():
            h = obj.get('hash')
            if not h:
                continue
            src = objects_dir / h[:2] / h
            dest = self.mc_dir / 'assets' / 'virtual' / virtual_name / name
            if not src.exists():
                continue
            if dest.exists() and dest.stat().st_size == src.stat().st_size:
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            except OSError:
                continue

    def _add_forge_modules(self, add, libraries):
        '''Forge 1.18+ 的 fmlcore 等模块不会写进版本 JSON，但运行时必需。'''

        forge_version = ''
        for key, lib in libraries.items():
            if key == 'net.minecraftforge:fmlloader':
                parts = str(lib.get('name') or '').split(':')
                if len(parts) > 2:
                    forge_version = parts[2]
                break
        if not forge_version:
            return

        def companion_matches(artifact):
            lib = libraries.get(f'net.minecraftforge:{artifact}')
            if not lib:
                return False
            parts = str(lib.get('name') or '').split(':')
            return len(parts) > 2 and parts[2] == forge_version

        for artifact in ('fmlcore', 'javafmllanguage', 'lowcodelanguage', 'mclanguage'):
            if companion_matches(artifact):
                continue
            path = f'net/minecraftforge/{artifact}/{forge_version}/{artifact}-{forge_version}.jar'
            add(FileTask(
                label=f'依赖库 net.minecraftforge:{artifact}:{forge_version}',
                urls=forge_companion_urls(forge_version, artifact),
                dest=self.mc_dir / 'libraries' / path,
                category='libraries', check_zip=True,
            ))
        universal_path = f'net/minecraftforge/forge/{forge_version}/forge-{forge_version}-universal.jar'
        add(FileTask(
            label=f'Forge 运行库 {forge_version} (universal)',
            urls=forge_universal_urls(forge_version),
            dest=self.mc_dir / 'libraries' / universal_path,
            category='libraries', check_zip=True,
        ))

    def _is_valid(self, task):
        mode = 'missing' if self.missing_only else 'fast' if self.fast else 'full'
        return task.valid(mode)

    def _repair_task(self, task):
        '''校验并下载单个文件；返回 (成功, 是否缺失, 错误, 标签, 是否可选)。'''

        if self.cancel():
            raise RepairCancelled()
        if self.fast and task.optional:
            return True, False, '', task.label, task.optional
        if self._is_valid(task):
            return True, False, '', task.label, task.optional
        if not task.label.startswith('资源 '):
            self.status(f'补全 {task.label} ...')
        try:
            self.downloader.download(task, cancel=self.cancel)
        except DownloadCancelled:
            if self.cancel():
                raise RepairCancelled()
            return False, True, '下载超时', task.label, task.optional
        except Exception as exc:
            return False, True, str(exc), task.label, task.optional
        return True, True, '', task.label, task.optional

    def repair(self):
        try:
            return self._repair()
        finally:
            self.downloader.close()

    def _repair(self):
        if self.cancel():
            raise RepairCancelled()
        self.phase('json', 0)
        chain = self._load_chain()
        self.phase('json', 100)
        self.log(f'开始校验版本: {chain[0][0]}（依赖链 {len(chain)} 层）')
        tasks, libraries = self._collect_tasks(chain)
        if self.missing_only:
            tasks = [task for task in tasks if task.category != 'assets']
        self.result.checked = len(tasks)
        total = max(1, len(tasks))
        self.status(f'正在校验 {len(tasks)} 个文件...')
        category_totals = {}
        category_done = {}
        for task in tasks:
            category_totals[task.category] = category_totals.get(task.category, 0) + 1
        for category in category_totals:
            category_done[category] = 0
            self.phase(category, 0)
        worker_count = max(1, min(DownloadGate.max_threads(), 32))
        completed = 0
        cancelled = False
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {pool.submit(self._repair_task, task): task for task in tasks}
            for future in as_completed(futures):
                if self.cancel():
                    cancelled = True
                    break
                task = futures[future]
                try:
                    success, attempted, error, label, optional = future.result()
                except RepairCancelled:
                    cancelled = True
                    break
                except Exception as exc:
                    success, attempted, error, label, optional = False, True, str(exc), '文件', False
                if attempted:
                    if success:
                        self.result.fixed += 1
                        self.log(f'已修复: {label}')
                    elif optional:
                        self.result.warnings.append(f'{label}: {error}')
                        self.log(f'可选文件未补全: {label} ({error})')
                    else:
                        self.result.failed += 1
                        if len(self.result.failed_items) < 8:
                            self.result.failed_items.append(label)
                        self.log(f'修复失败: {label} ({error})')
                category = task.category
                category_done[category] = category_done.get(category, 0) + 1
                self.phase(category, category_done[category] * 100 / max(1, category_totals.get(category, 1)))
                completed += 1
                self._emit_progress(min(99, int(completed * 100 / total)))
                if completed % 25 == 0:
                    self.status(f'校验进度 {completed}/{len(tasks)}，已修复 {self.result.fixed} 个')
        if cancelled or self.cancel():
            raise RepairCancelled()
        for category in category_totals:
            self.phase(category, 100)
        self._ensure_forge_runtime(chain, libraries)
        self.status('正在校验并解压 natives...')
        for index_data, virtual_dir in self._virtual_assets:
            self._extract_virtual_assets(index_data, virtual_dir)
        self._extract_natives(chain, libraries)
        self._emit_progress(100, force=True)
        return self.result

    def _emit_progress(self, value, force=False):
        now = time.monotonic()
        if not force and now - self._progress_last < 0.08:
            return
        self._progress_last = now
        self.progress(value)

    def _ensure_forge_runtime(self, chain, libraries):
        '''Forge 1.17+ 还需要 installer 生成的 universal/client 两个 jar。'''

        forge_version = ''
        entries = libraries.values() if isinstance(libraries, dict) else libraries
        for lib in entries:
            name = str((lib or {}).get('name') or '')
            if name.startswith('net.minecraftforge:fmlloader:'):
                parts = name.split(':')
                if len(parts) > 2:
                    forge_version = parts[2]
                break
        if not forge_version:
            return
        mc_version = forge_version.split('-', 1)[0]
        candidates = [
            self.versions_dir / chain[0][0] / f'{chain[0][0]}.jar',
            self.versions_dir / mc_version / f'{mc_version}.jar',
        ]
        candidates += [self.versions_dir / dir_id / f'{dir_id}.jar' for dir_id, _dir, _data in chain]
        minecraft_jar = next((path for path in candidates if path.is_file()), None)
        try:
            from core.forge_artifacts import ensure_forge_artifacts
            changed = ensure_forge_artifacts(
                mc_version, forge_version, self.mc_dir,
                minecraft_jar=minecraft_jar,
                status_cb=self.status,
                progress_cb=(lambda value: self.phase('libraries', value)),
                log_cb=self.log,
                cancel=self.cancel,
            )
            if changed:
                self.log(f'Forge {forge_version} 运行时组件已补齐')
                self.phase('libraries', 100)
        except (RepairCancelled, DownloadCancelled):
            if self.cancel():
                raise RepairCancelled()
        except Exception as exc:
            self.result.failed += 1
            reason = str(exc).strip().splitlines()[0][:120] if str(exc).strip() else '未知错误'
            if len(self.result.failed_items) < 8:
                self.result.failed_items.append(f'Forge 客户端补丁（{reason}）')
            self.log(f'Forge 运行时组件补全失败: {exc}')
            logger.warning('Forge 运行时组件补全失败: %s', exc, exc_info=True)

    def _extract_natives(self, chain, libraries):
        version_id = chain[0][0]
        natives_dir = self.versions_dir / version_id / 'natives'
        natives_dir.mkdir(parents=True, exist_ok=True)
        for lib in libraries:
            try:
                native = get_natives(lib)
            except Exception:
                native = ''
            if not native:
                continue
            downloads = lib.get('downloads') or {}
            classifier = (downloads.get('classifiers') or {}).get(native) or {}
            jar_path = None
            if classifier.get('path'):
                jar_path = self.mc_dir / 'libraries' / classifier['path']
            else:
                path = _maven_path(lib.get('name', ''), native)
                if path:
                    jar_path = self.mc_dir / 'libraries' / path
            if not jar_path or not jar_path.is_file():
                continue
            exclude = (lib.get('extract') or {}).get('exclude') or []
            exclude = [e.replace('/', os.sep) for e in exclude]
            try:
                with zipfile.ZipFile(jar_path) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        name = info.filename
                        if name.startswith('META-INF/'):
                            continue
                        if any((name.startswith(e) for e in exclude)):
                            continue
                        target = natives_dir / Path(name).name
                        if target.exists():
                            continue
                        with zf.open(info) as src:
                            with open(target, 'wb') as dst:
                                shutil.copyfileobj(src, dst)
            except (zipfile.BadZipFile, OSError) as exc:
                self.log(f'natives 解压失败 {jar_path.name}: {exc}')
                continue


def libraries_list(libs):
    return list(libs.values())

class GameRepairWorker(QThread):
    status_updated = Signal(str)
    progress_updated = Signal(int)
    log_message = Signal(str)
    finished_ok = Signal(str)
    finished_error = Signal(str)
    def __init__(self, version_id, mc_dir, parent=None):
        super().__init__(parent)
        self.version_id = version_id
        self.mc_dir = mc_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            engine = RepairEngine(self.version_id, self.mc_dir, status=self.status_updated.emit, progress=self.progress_updated.emit, log=self.log_message.emit, cancel=(lambda: self._cancelled))
            result = engine.repair()
        except RepairCancelled:
            self.finished_error.emit('修复已取消')
            return
        except Exception as exc:
            self.finished_error.emit(f'修复失败：{exc}')
            return
        if self._cancelled:
            self.finished_error.emit('修复已取消')
            return
        message = result.summary()
        if result.failed_items:
            message += '\n失败文件：\n' + '\n'.join(result.failed_items)
        self.finished_ok.emit(message)


# WARNING: Decompyle incomplete
