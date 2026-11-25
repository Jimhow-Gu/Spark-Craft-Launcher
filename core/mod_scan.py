'''轻量 Mod 扫描：递归 mods 目录，并读取 Jar 内 JiJ 嵌套模块。'''

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

MOD_SUFFIXES = ('.jar', '.litemod')
NESTED_PREFIXES = (
    'meta-inf/jars/',
    'meta-inf/jarjar/',
    'meta-inf/libraries/',
)


def normalize_mod_id(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').strip().lower())


def iter_mod_files(mods_dir, include_disabled=False):
    root = Path(mods_dir)
    if not root.is_dir():
        return []
    result = []
    try:
        paths = root.rglob('*')
        for path in paths:
            if not path.is_file():
                continue
            name = path.name.lower()
            if include_disabled and name.endswith('.jar.disabled'):
                result.append(path)
                continue
            if name.endswith(MOD_SUFFIXES):
                result.append(path)
    except OSError:
        return result
    result.sort(key=lambda path: str(path).lower())
    return result


def _read_ids_from_zip(zf):
    ids = set()
    names = set(zf.namelist())
    lowered = {name.lower(): name for name in names}
    for meta_name in ('fabric.mod.json', 'quilt.mod.json'):
        actual = lowered.get(meta_name)
        if not actual:
            continue
        try:
            meta = json.loads(zf.read(actual).decode('utf-8', 'replace'))
        except Exception:
            continue
        if isinstance(meta, dict):
            value = meta.get('id')
            if value:
                ids.add(normalize_mod_id(value))
            quilt = meta.get('quilt_loader')
            if isinstance(quilt, dict) and quilt.get('id'):
                ids.add(normalize_mod_id(quilt.get('id')))
        break
    for meta_name in ('meta-inf/mods.toml', 'meta-inf/neoforge.mods.toml'):
        actual = lowered.get(meta_name)
        if not actual:
            continue
        try:
            text = zf.read(actual).decode('utf-8', 'replace')
        except Exception:
            continue
        for value in re.findall(
            r'(?im)^\s*modId\s*=\s*["\']([^"\']+)', text
        ):
            ids.add(normalize_mod_id(value))
    actual = lowered.get('mcmod.info')
    if actual:
        try:
            data = json.loads(zf.read(actual).decode('utf-8', 'replace'))
            if isinstance(data, dict):
                data = [data]
            for item in data if isinstance(data, list) else []:
                if isinstance(item, dict) and item.get('modid'):
                    ids.add(normalize_mod_id(item.get('modid')))
        except Exception:
            pass
    return {value for value in ids if value}


def _first(pattern, text):
    match = re.search(pattern, text, re.I | re.M)
    return match.group(1).strip() if match else ''


def _metadata_from_zip(zf, fallback=''):
    names = set(zf.namelist())
    lowered = {name.lower(): name for name in names}
    for meta_name in ('fabric.mod.json', 'quilt.mod.json'):
        actual = lowered.get(meta_name)
        if not actual:
            continue
        try:
            meta = json.loads(zf.read(actual).decode('utf-8', 'replace'))
        except Exception:
            continue
        if isinstance(meta, dict):
            return [{
                'modid': normalize_mod_id(meta.get('id')),
                'name': str(meta.get('name') or meta.get('id') or fallback),
                'version': str(meta.get('version') or ''),
                'loader': 'Fabric' if meta_name == 'fabric.mod.json' else 'Quilt',
                'description': str(meta.get('description') or ''),
            }]
    for meta_name in ('meta-inf/neoforge.mods.toml', 'meta-inf/mods.toml'):
        actual = lowered.get(meta_name)
        if not actual:
            continue
        try:
            text = zf.read(actual).decode('utf-8', 'replace')
        except Exception:
            continue
        loader = 'NeoForge' if 'neoforge' in meta_name else 'Forge'
        mod_ids = re.findall(r'(?im)^\s*modId\s*=\s*["\']([^"\']+)', text)
        names_found = re.findall(r'(?im)^\s*displayName\s*=\s*["\']([^"\']+)', text)
        versions = re.findall(r'(?im)^\s*version\s*=\s*["\']([^"\']+)', text)
        result = []
        for index, mod_id in enumerate(mod_ids or [fallback]):
            result.append({
                'modid': normalize_mod_id(mod_id),
                'name': names_found[index] if index < len(names_found) else mod_id or fallback,
                'version': versions[index] if index < len(versions) else '',
                'loader': loader,
                'description': '',
            })
        return result
    actual = lowered.get('mcmod.info')
    if actual:
        try:
            data = json.loads(zf.read(actual).decode('utf-8', 'replace'))
            if isinstance(data, dict):
                data = [data]
            result = []
            for item in data if isinstance(data, list) else []:
                if isinstance(item, dict):
                    result.append({
                        'modid': normalize_mod_id(item.get('modid')),
                        'name': str(item.get('name') or item.get('modid') or fallback),
                        'version': str(item.get('version') or ''),
                        'loader': 'Forge',
                        'description': str(item.get('description') or ''),
                    })
            if result:
                return result
        except Exception:
            pass
    return []


def _nested_jars(zf):
    for name in zf.namelist():
        lowered = name.replace(chr(92), '/').lower()
        if not lowered.endswith('.jar'):
            continue
        if any(lowered.startswith(prefix) for prefix in NESTED_PREFIXES):
            try:
                yield zf.read(name)
            except Exception:
                continue


FABRIC_SYSTEM_MODULES = (
    ('fabricloader', 'Fabric Loader'),
    ('mixinextras', 'MixinExtras'),
    ('java', 'Java'),
    ('minecraft', 'Minecraft'),
)


def has_fabric_modules(ids):
    return any(str(value).startswith('fabric') for value in ids or ())


def fabric_system_ids(ids):
    if not has_fabric_modules(ids):
        return set()
    return {modid for modid, _name in FABRIC_SYSTEM_MODULES}


def fabric_system_infos():
    return [
        {
            'nested': True,
            'system': True,
            'modid': modid,
            'name': name,
            'version': '',
            'loader': 'Fabric',
            'description': '游戏运行时系统模块（不计入 mods 文件夹）',
            'outer': '',
            'path': None,
            'filename': '（系统模块）',
            'enabled': True,
            'icon': b'',
            'update_available': False,
        }
        for modid, name in FABRIC_SYSTEM_MODULES
    ]


def nested_mod_infos(mods_dir, include_disabled=False):
    """返回 Jar-in-Jar 内置模块的只读展示信息。"""

    result = []
    for path in iter_mod_files(mods_dir, include_disabled=include_disabled):
        try:
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    lowered = name.replace(chr(92), '/').lower()
                    if not lowered.endswith('.jar'):
                        continue
                    if not any(lowered.startswith(prefix) for prefix in NESTED_PREFIXES):
                        continue
                    try:
                        data = zf.read(name)
                        with zipfile.ZipFile(io.BytesIO(data)) as nested:
                            metas = _metadata_from_zip(
                                nested, fallback=Path(name).stem
                            )
                    except (OSError, zipfile.BadZipFile):
                        metas = []
                    for meta in metas:
                        item = dict(meta)
                        item.update({
                            'nested': True,
                            'outer': str(path),
                            'path': None,
                            'filename': f'{path.name} / {Path(name).name}',
                            'enabled': True,
                            'icon': b'',
                            'update_available': False,
                        })
                        result.append(item)
        except (OSError, zipfile.BadZipFile):
            continue
    return result


def scan_mod_ids(mods_dir, include_disabled=False):
    '''返回去重后的 modid 集合；无元数据的顶层 Jar 用文件名兜底。'''

    ids = set()
    for path in iter_mod_files(mods_dir, include_disabled=include_disabled):
        outer_ids = set()
        nested_ids = set()
        try:
            with zipfile.ZipFile(path) as zf:
                outer_ids = _read_ids_from_zip(zf)
                for data in _nested_jars(zf):
                    try:
                        with zipfile.ZipFile(io.BytesIO(data)) as nested:
                            nested_ids |= _read_ids_from_zip(nested)
                    except (OSError, zipfile.BadZipFile):
                        continue
        except (OSError, zipfile.BadZipFile):
            pass
        ids |= outer_ids
        ids |= nested_ids
        if not outer_ids and not nested_ids:
            stem = path.name
            if stem.lower().endswith('.disabled'):
                stem = stem[:-len('.disabled')]
            if stem.lower().endswith(('.jar', '.litemod')):
                stem = stem.rsplit('.', 1)[0]
            token = normalize_mod_id(stem)
            if token:
                ids.add(token)
    return ids


def count_mod_ids(mods_dir):
    return len(scan_mod_ids(mods_dir))


__all__ = [
    'FABRIC_SYSTEM_MODULES', 'MOD_SUFFIXES', 'count_mod_ids',
    'fabric_system_ids', 'fabric_system_infos', 'iter_mod_files',
    'nested_mod_infos', 'normalize_mod_id', 'scan_mod_ids',
]
