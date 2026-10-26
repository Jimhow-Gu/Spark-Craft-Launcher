'''把版本导出为 Modrinth .mrpack 整合包（本启动器 / PCL2 / HMCL / Modrinth 均可导入）。'''

from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.mods import ModrinthSource
from core.version import detect_version_loader

LOADER_DEP_KEYS = {
    'fabric': 'fabric-loader',
    'quilt': 'quilt-loader',
    'forge': 'forge',
    'neoforge': 'neoforge',
}
LOADER_PATTERNS = {
    'fabric': r'fabric-loader:([^:@]+)',
    'forge': r'minecraftforge:forge:([^:@]+)',
    'neoforge': r'neoforged:neoforge:([^:@]+)',
    'quilt': r'quilt-loader:([^:@]+)',
    'optifine': r'optifine:OptiFine:([^:@]+)',
}
# 游戏本体设置包含的文件（键位、音量、视频等）
GAME_SETTINGS_FILES = ('options.txt', 'optionsof.txt', 'optionsshaders.txt')
# 游戏本体个人信息（命令历史、快捷栏等）
PERSONAL_FILES = ('hotbar.nbt', 'command_history.txt', 'command_history.txt.old')


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError):
        return {}


def read_chain(mc_dir, version_id):
    chain = []
    current = version_id
    seen = set()
    versions_dir = Path(mc_dir) / 'versions'
    while current and current not in seen and len(chain) < 10:
        seen.add(current)
        version_dir = versions_dir / current
        data = _read_json(version_dir / f'{current}.json')
        chain.append((current, data, version_dir))
        parent = str(data.get('inheritsFrom') or '').strip()
        if not parent:
            break
        current = parent
    return chain


def detect_version_info(mc_dir, version_id):
    '''识别版本的原版 MC 版本与加载器信息（与修改页一致的推断顺序）。'''

    chain = read_chain(mc_dir, version_id)
    base_mc = ''
    pack = _read_json(Path(mc_dir) / 'versions' / version_id / 'pmgl_pack.json')
    if pack.get('mc_version'):
        base_mc = str(pack['mc_version'])
    if not base_mc:
        for _vid, data, _dir in chain:
            marker = str((data or {}).get('pmgl_mc') or '').strip()
            if marker:
                base_mc = marker
                break
    if not base_mc:
        top_id = chain[-1][0]
        for _vid, data, _dir in reversed(chain):
            for lib in (data or {}).get('libraries') or []:
                name = str((lib or {}).get('name') or '')
                match = re.search(r'net\.minecraftforge:forge:([^:@]+)', name) \
                    or re.search(r'optifine:OptiFine:([^:@]+)', name)
                if match:
                    head = re.split(r'[-_]', match.group(1), 1)[0]
                    if re.fullmatch(r'\d+(?:\.\d+)*', head):
                        base_mc = head
                        break
                match = re.search(r'net\.neoforged:neoform:(\d+\.\d+(?:\.\d+)?)-', name)
                if match:
                    base_mc = match.group(1)
                    break
            if base_mc:
                break
    if not base_mc:
        top_id = chain[-1][0]
        for pattern in (
            r'(\d+(?:\.\d+)+)-forge-',
            r'(\d+(?:\.\d+)+)-neoforge-',
            r'fabric-loader-[\w.]+-(\d[\w.]*)$',
            r'quilt-loader-[\w.]+-(\d[\w.]*)$',
        ):
            match = re.match(pattern, top_id, re.I)
            if match:
                base_mc = match.group(1)
                break
    base_mc = base_mc or (chain[-1][0] if chain else version_id)

    loader = ''
    for vid, data, version_dir in chain:
        try:
            loader = detect_version_loader(vid, data, version_dir)
        except Exception:
            loader = ''
        if loader in ('fabric', 'forge', 'neoforge', 'quilt'):
            break
    loader_version = ''
    if loader:
        pattern = LOADER_PATTERNS.get(loader)
        if pattern:
            for _vid, data, _dir in chain:
                for lib in (data or {}).get('libraries') or []:
                    match = re.search(pattern, str((lib or {}).get('name') or ''), re.I)
                    if match:
                        value = match.group(1)
                        if loader == 'forge' and value.startswith(base_mc + '-'):
                            value = value[len(base_mc) + 1:]
                        loader_version = value
                        break
                if loader_version:
                    break
    return {
        'base_mc': base_mc,
        'loader': loader if loader in LOADER_DEP_KEYS else '',
        'loader_version': loader_version,
    }


class PackExportWorker(QThread):
    status = Signal(str)
    progress = Signal(int)
    finished_ok = Signal(str)
    finished_error = Signal(str)

    def __init__(self, version_id, mc_dir, game_dir, out_path, name, pack_version,
                 selections, modrinth_only=False, summary=''):
        super().__init__()
        self.version_id = version_id
        self.mc_dir = Path(mc_dir)
        self.game_dir = Path(game_dir)
        self.out_path = Path(out_path)
        self.name = str(name or version_id)
        self.pack_version = str(pack_version or '1.0.0')
        self.selections = dict(selections or {})
        self.modrinth_only = bool(modrinth_only)
        self.summary = str(summary or '')
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # ---------- 文件收集 ----------
    def _collect_files(self):
        """按勾选项收集 (绝对路径, 相对游戏目录的 POSIX 路径) 列表。"""

        pairs = []
        game_dir = self.game_dir

        def add_file(path):
            path = Path(path)
            try:
                if path.is_file() and path.stat().st_size > 0:
                    pairs.append((path, path.relative_to(game_dir).as_posix()))
            except (OSError, ValueError):
                pass

        def add_dir_files(dir_path):
            dir_path = Path(dir_path)
            try:
                for item in sorted(dir_path.rglob('*')):
                    if item.is_file():
                        add_file(item)
            except OSError:
                pass

        if self.selections.get('game_settings'):
            for name in GAME_SETTINGS_FILES:
                add_file(game_dir / name)
        if self.selections.get('personal'):
            for name in PERSONAL_FILES:
                add_file(game_dir / name)
        mods = []
        if self.selections.get('mods'):
            mods_dir = game_dir / 'mods'
            try:
                mods = sorted(
                    (p for p in mods_dir.glob('*.jar') if p.is_file()),
                    key=lambda p: p.name.lower(),
                )
            except OSError:
                mods = []
        self._mod_files = mods
        if self.selections.get('mod_config') and self.selections.get('mods'):
            add_dir_files(game_dir / 'config')
        rp = self.selections.get('resourcepacks')
        if rp:
            for name in rp:
                add_file(game_dir / 'resourcepacks' / name)
        sp = self.selections.get('shaderpacks')
        if sp:
            for name in sp:
                add_file(game_dir / 'shaderpacks' / name)
        if self.selections.get('screenshots'):
            add_dir_files(game_dir / 'screenshots')
        if self.selections.get('schematics'):
            add_dir_files(game_dir / 'schematics')
        if self.selections.get('saves'):
            add_dir_files(game_dir / 'saves')
        if self.selections.get('servers'):
            add_file(game_dir / 'servers.dat')
            add_file(game_dir / 'servers.dat_old')
        self._override_pairs = pairs
        if self.selections.get('pack_resources'):
            self._collect_shared_resources()
        return pairs

    def _collect_shared_resources(self):
        '''打包资源文件：版本引用的库文件 + 资源索引与对象，导入时无需重新下载。'''

        extra = []
        seen = set()
        chain = read_chain(self.mc_dir, self.version_id)
        chain_ids = {vid for vid, _data, _dir in chain}
        base_mc = ''
        pack = _read_json(
            Path(self.mc_dir) / 'versions' / self.version_id / 'pmgl_pack.json'
        )
        if pack.get('mc_version'):
            base_mc = str(pack['mc_version'])
        if not base_mc:
            for _vid, data, _dir in chain:
                marker = str((data or {}).get('pmgl_mc') or '').strip()
                if marker:
                    base_mc = marker
                    break
        libraries_root = self.mc_dir / 'libraries'
        assets_root = self.mc_dir / 'assets'

        def add_shared(path, rel):
            path = Path(path)
            key = rel.lower()
            if key in seen:
                return
            try:
                if path.is_file() and path.stat().st_size > 0:
                    extra.append((path, rel))
                    seen.add(key)
            except OSError:
                pass

        sources = [(vid, data) for vid, data, _dir in chain]
        # 老版 Forge 的继承链在加载器 JSON 处断开，原版版本不在链上，需补入
        if base_mc and base_mc not in chain_ids:
            base_dir = self.mc_dir / 'versions' / base_mc
            base_data = _read_json(base_dir / f'{base_mc}.json')
            if base_data:
                sources.append((base_mc, base_data))
        for _vid, data in sources:
            for lib in (data or {}).get('libraries') or []:
                if not isinstance(lib, dict) or not lib.get('name'):
                    continue
                downloads = lib.get('downloads') or {}
                artifact = downloads.get('artifact') or {}
                rel_path = str(artifact.get('path') or '')
                if not rel_path:
                    name = str(lib['name'])
                    parts = name.split('@', 1)[0].split(':')
                    if len(parts) < 3:
                        continue
                    group, artifact_id, version = parts[0], parts[1], parts[2]
                    rel_path = f'{group.replace(".", "/")}/{artifact_id}/{version}/{artifact_id}-{version}.jar'
                add_shared(libraries_root / rel_path, f'libraries/{rel_path}')
        try:
            asset_index_id = ''
            for _vid, data in sources:
                asset_index = (data or {}).get('assetIndex') or {}
                asset_index_id = str(asset_index.get('id') or data.get('assets') or '')
                if asset_index_id:
                    break
            if asset_index_id:
                index_file = assets_root / 'indexes' / f'{asset_index_id}.json'
                index_data = _read_json(index_file)
                add_shared(index_file, f'assets/indexes/{asset_index_id}.json')
                for _name, obj in (index_data.get('objects') or {}).items():
                    if not isinstance(obj, dict):
                        continue
                    obj_hash = str(obj.get('hash') or '')
                    if len(obj_hash) >= 2:
                        add_shared(
                            assets_root / 'objects' / obj_hash[:2] / obj_hash,
                            f'assets/objects/{obj_hash[:2]}/{obj_hash}',
                        )
        except OSError:
            pass
        self._override_pairs = self._override_pairs + extra

    def _hash_file(self, path, algo):
        import hashlib
        digest = hashlib.new(algo)
        try:
            with open(path, 'rb') as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return ''

    def _modrinth_match(self, path, sha1):
        '''返回 (下载 URL, sha512 或 '')；未匹配返回 (None, '')。'''

        version = self._source.get_version_by_hash(sha1)
        if not isinstance(version, dict):
            return None, ''
        files = version.get('files') or []
        chosen = None
        for item in files:
            if not isinstance(item, dict) or not item.get('url'):
                continue
            hashes = item.get('hashes') or {}
            if str(hashes.get('sha1') or '').lower() == sha1.lower():
                chosen = item
                break
        if chosen is None:
            chosen = next(
                (f for f in files if isinstance(f, dict) and f.get('primary') and f.get('url')),
                None,
            )
        if chosen is None:
            return None, ''
        remote_sha512 = ''
        for item in files:
            if isinstance(item, dict) and str((item.get('hashes') or {}).get('sha1') or '').lower() == sha1.lower():
                remote_sha512 = str((item.get('hashes') or {}).get('sha512') or '')
                break
        return str(chosen.get('url') or ''), remote_sha512

    def run(self):
        source = ModrinthSource()
        self._source = source
        try:
            self.status.emit('正在收集版本文件...')
            self.progress.emit(2)
            info = detect_version_info(self.mc_dir, self.version_id)
            pairs = self._collect_files()

            index_files = []
            overrides = []
            skipped = []
            total = len(self._mod_files) + len(pairs) + 1
            done = 0

            def tick():
                nonlocal done
                done += 1
                self.progress.emit(min(96, int(done * 96 / max(1, total))))

            # Mod：优先匹配 Modrinth（导入端直接下载），匹配不到的放入 overrides
            if self.selections.get('mods'):
                for path in self._mod_files:
                    if self._cancelled:
                        raise InterruptedError()
                    rel = path.relative_to(self.game_dir).as_posix()
                    self.status.emit(f'正在匹配 Mod {path.name}...')
                    sha1 = self._hash_file(path, 'sha1')
                    url, remote_sha512 = self._modrinth_match(path, sha1) if sha1 else (None, '')
                    if url:
                        sha512 = remote_sha512 or self._hash_file(path, 'sha512')
                        index_files.append({
                            'path': rel,
                            'hashes': {'sha1': sha1, 'sha512': sha512},
                            'env': {'client': 'required', 'server': 'unsupported'},
                            'downloads': [url],
                            'fileSize': path.stat().st_size,
                        })
                    elif self.modrinth_only:
                        skipped.append(path.name)
                    else:
                        overrides.append((path, rel))
                    tick()

            for path, rel in self._override_pairs:
                if self._cancelled:
                    raise InterruptedError()
                self.status.emit(f'正在打包 {rel}...')
                overrides.append((path, rel))
                tick()

            if skipped:
                self.status.emit(f'{len(skipped)} 个 Mod 未在 Modrinth 找到，已跳过')
            if not index_files and not overrides:
                raise RuntimeError('没有可导出的内容，请至少勾选一项')

            dependencies = {'minecraft': info['base_mc']}
            dep_key = LOADER_DEP_KEYS.get(info['loader'])
            if dep_key and info['loader_version']:
                version = info['loader_version']
                if info['loader'] in ('forge', 'neoforge') and not version.startswith(info['base_mc'] + '-'):
                    version = f'{info["base_mc"]}-{version}'
                dependencies[dep_key] = version

            index = {
                'formatVersion': 1,
                'game': 'minecraft',
                'versionId': self.pack_version,
                'name': self.name,
                'files': index_files,
                'dependencies': dependencies,
            }
            if self.summary:
                index['summary'] = self.summary

            self.status.emit('正在写入整合包...')
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(self.out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('modrinth.index.json', json.dumps(index, ensure_ascii=False, indent=2))
                for path, rel in overrides:
                    if self._cancelled:
                        raise InterruptedError()
                    zf.write(path, f'overrides/{rel}')
                    tick()
            self.progress.emit(100)
            if self._cancelled:
                try:
                    self.out_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self.finished_error.emit('导出已取消')
                return
            self.finished_ok.emit(str(self.out_path))
        except InterruptedError:
            try:
                self.out_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.finished_error.emit('导出已取消')
        except Exception as exc:
            try:
                self.out_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.finished_error.emit(f'导出失败：{exc}')
        finally:
            try:
                source.http.close()
            except Exception:
                pass
