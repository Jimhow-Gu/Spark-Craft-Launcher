"""版本重命名计划、两阶段提交和 journal 恢复。"""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.paths import safe_rmtree

BACKUP_PREFIX = '.rename-backup-'
JOURNAL_NAME = '.rename-journal.json'
CONFIG_KEYS = (
    'selected_version',
    'favorited_versions',
    'version_settings',
    'version_nicknames',
)


class RenameError(Exception):
    pass


class _SimulatedCrash(Exception):
    pass


@dataclass
class RenamePlan:
    old_id: str
    new_id: str
    old_dir: Path
    new_dir: Path
    main_name: str
    main_bytes: bytes
    main_new_bytes: bytes
    new_main_name: str
    children: list = field(default_factory=list)
    profile_path: Optional[Path] = None
    profile_bytes: Optional[bytes] = None
    profile_new_bytes: Optional[bytes] = None
    config: object = None
    config_path: Optional[Path] = None
    config_bytes: Optional[bytes] = None
    config_existed: bool = False
    config_snapshot: dict = field(default_factory=dict)
    config_new: dict = field(default_factory=dict)
    config_changed: bool = False
    old_jar: Optional[Path] = None
    new_jar: Optional[Path] = None
    backup_dir: Path = Path()
    journal_path: Path = Path()


def _atomic_write(path: Path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with open(tmp, 'wb') as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _json_bytes(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError) as exc:
        raise RenameError(f'无法读取 JSON：{path}（{exc}）') from exc


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attrs = os.lstat(str(path)).st_file_attributes
        return bool(attrs & 0x400)
    except (OSError, AttributeError):
        return False


def _valid_name(name: str) -> str:
    if not name or name in ('.', '..'):
        return '版本名称不能为空'
    if re.search('[<>:"/\\\\|?*\\x00-\\x1f]', name):
        return '版本名称包含非法字符'
    if name.endswith((' ', '.')):
        return '版本名称不能以空格或点结尾'
    reserved = {
        'CON', 'PRN', 'AUX', 'NUL',
        *{f'COM{i}' for i in range(1, 10)},
        *{f'LPT{i}' for i in range(1, 10)},
    }
    if name.split('.', 1)[0].strip().upper() in reserved:
        return '版本名称是 Windows 保留名称'
    return ''


def build_rename_plan(
    old_id: str,
    new_id: str,
    versions_dir: Path,
    mc_dir: Path,
    config,
    *,
    running_check: Optional[Callable[[str], bool]] = None,
) -> RenamePlan:
    old_id = str(old_id or '').strip()
    new_id = str(new_id or '').strip()
    if old_id == new_id:
        raise RenameError('__NOOP__')
    error = _valid_name(new_id)
    if error:
        raise RenameError(error)
    if running_check is not None and running_check(old_id):
        raise RenameError('该版本的游戏正在运行，请先关闭游戏后再重命名')
    versions_dir = Path(versions_dir)
    old_dir = versions_dir / old_id
    new_dir = versions_dir / new_id
    if not old_dir.is_dir():
        raise RenameError('旧版本目录不存在')
    if _is_reparse(old_dir):
        raise RenameError('版本目录是 symlink/junction，拒绝直接重命名')
    if new_dir.exists():
        raise RenameError('目标版本名称已存在')
    try:
        for item in versions_dir.iterdir():
            if item.name.casefold() == new_id.casefold():
                raise RenameError('目标名称与现有文件夹大小写冲突')
    except OSError as exc:
        raise RenameError(f'无法读取 versions 目录：{exc}') from exc
    journal_path = versions_dir / JOURNAL_NAME
    if journal_path.exists():
        raise RenameError('检测到未完成的重命名 journal，请先重启启动器完成恢复')

    preferred = old_dir / f'{old_id}.json'
    if preferred.is_file():
        main_path = preferred
    else:
        candidates = [path for path in old_dir.glob('*.json') if path.is_file()]
        if len(candidates) != 1:
            raise RenameError('版本目录必须包含唯一的版本 JSON')
        main_path = candidates[0]
    main_data = _read_json(main_path)
    if not isinstance(main_data, dict):
        raise RenameError('版本 JSON 不是对象')
    main_name = main_path.name
    new_main_name = f'{new_id}.json'
    new_main = copy.deepcopy(main_data)
    new_main['id'] = new_id
    if new_main.get('jar') == old_id:
        new_main['jar'] = new_id

    children = []
    for child_path in sorted(versions_dir.glob('*/*.json')):
        if child_path == main_path or not child_path.is_file():
            continue
        data = _read_json(child_path)
        if not isinstance(data, dict):
            raise RenameError(f'子版本 JSON 不是对象：{child_path}')
        changed = False
        if data.get('inheritsFrom') == old_id:
            data = copy.deepcopy(data)
            data['inheritsFrom'] = new_id
            changed = True
        if data.get('jar') == old_id:
            data = copy.deepcopy(data)
            data['jar'] = new_id
            changed = True
        if changed:
            children.append((child_path, child_path.read_bytes(), _json_bytes(data)))

    profile_path = Path(mc_dir) / 'launcher_profiles.json'
    profile_bytes = None
    profile_new_bytes = None
    if profile_path.is_file():
        profile_bytes = profile_path.read_bytes()
        profile_data = _read_json(profile_path)
        if not isinstance(profile_data, dict):
            raise RenameError('launcher_profiles.json 格式不正确')
        profiles = profile_data.get('profiles')
        profile_changed = False
        if isinstance(profiles, dict):
            for value in profiles.values():
                if isinstance(value, dict) and value.get('lastVersionId') == old_id:
                    value['lastVersionId'] = new_id
                    profile_changed = True
        if profile_changed:
            profile_new_bytes = _json_bytes(profile_data)

    config_path = Path(getattr(config, 'config_file', '') or '')
    config_existed = bool(str(config_path) and config_path.is_file())
    config_bytes = None
    if config_existed:
        try:
            config_bytes = config_path.read_bytes()
        except OSError:
            config_bytes = None
    snapshot = {key: copy.deepcopy(config.get(key)) for key in CONFIG_KEYS}
    config_new = copy.deepcopy(snapshot)
    config_changed = False
    if snapshot.get('selected_version') == old_id:
        config_new['selected_version'] = new_id
        config_changed = True
    favorites = snapshot.get('favorited_versions') or []
    if not isinstance(favorites, (list, tuple)):
        favorites = []
    favorites = list(favorites)
    if old_id in favorites:
        config_new['favorited_versions'] = list(dict.fromkeys(
            new_id if value == old_id else value for value in favorites
        ))
        config_changed = True
    settings = snapshot.get('version_settings') or {}
    settings = dict(settings) if isinstance(settings, dict) else {}
    if old_id in settings:
        settings[new_id] = settings.pop(old_id)
        config_new['version_settings'] = settings
        config_changed = True
    nicknames = snapshot.get('version_nicknames') or {}
    nicknames = dict(nicknames) if isinstance(nicknames, dict) else {}
    if old_id in nicknames:
        nicknames[new_id] = nicknames.pop(old_id)
        config_new['version_nicknames'] = nicknames
        config_changed = True

    old_jar = old_dir / f'{old_id}.jar'
    new_jar = old_dir / f'{new_id}.jar'
    token = uuid.uuid4().hex[:10]
    return RenamePlan(
        old_id=old_id,
        new_id=new_id,
        old_dir=old_dir,
        new_dir=new_dir,
        main_name=main_name,
        main_bytes=main_path.read_bytes(),
        main_new_bytes=_json_bytes(new_main),
        new_main_name=new_main_name,
        children=children,
        profile_path=profile_path if profile_bytes is not None else None,
        profile_bytes=profile_bytes,
        profile_new_bytes=profile_new_bytes,
        config=config,
        config_path=config_path if str(config_path) else None,
        config_bytes=config_bytes,
        config_existed=config_existed,
        config_snapshot=snapshot,
        config_new=config_new,
        config_changed=config_changed,
        old_jar=old_jar if old_jar.is_file() else None,
        new_jar=new_jar if new_jar.is_file() else None,
        backup_dir=versions_dir / f'{BACKUP_PREFIX}{token}',
        journal_path=journal_path,
    )


def _journal_data(plan: RenamePlan, phase: str, jar_moved: bool = False) -> dict:
    children = []
    for index, (path, _original, _new) in enumerate(plan.children):
        children.append({
            'path': str(path),
            'backup': f'child-{index:03d}.json.orig',
        })
    return {
        'schema': 1,
        'phase': phase,
        'old_id': plan.old_id,
        'new_id': plan.new_id,
        'old_dir': str(plan.old_dir),
        'new_dir': str(plan.new_dir),
        'main_name': plan.main_name,
        'new_main_name': plan.new_main_name,
        'backup_dir': str(plan.backup_dir),
        'children': children,
        'profile_path': str(plan.profile_path) if plan.profile_path else '',
        'profile_backup': 'launcher_profiles.json.orig' if plan.profile_bytes is not None else '',
        'config_path': str(plan.config_path) if plan.config_path else '',
        'config_backup': 'config.json.orig' if plan.config_bytes is not None else '',
        'config_existed': bool(plan.config_existed),
        'jar_old_name': f'{plan.old_id}.jar',
        'jar_new_name': f'{plan.new_id}.jar',
        'jar_moved': bool(jar_moved),
    }


def _write_journal(plan: RenamePlan, phase: str, jar_moved: bool = False):
    _atomic_write(plan.journal_path, _json_bytes(_journal_data(plan, phase, jar_moved)))


def _write_backups(plan: RenamePlan):
    plan.backup_dir.mkdir(parents=True, exist_ok=False)
    _atomic_write(plan.backup_dir / 'main.json.orig', plan.main_bytes)
    for index, (_path, original, _new) in enumerate(plan.children):
        _atomic_write(plan.backup_dir / f'child-{index:03d}.json.orig', original)
    if plan.profile_bytes is not None:
        _atomic_write(plan.backup_dir / 'launcher_profiles.json.orig', plan.profile_bytes)
    if plan.config_bytes is not None:
        _atomic_write(plan.backup_dir / 'config.json.orig', plan.config_bytes)


def _apply_config(plan: RenamePlan):
    if not plan.config_changed:
        return
    config = plan.config
    for key in CONFIG_KEYS:
        if plan.config_new.get(key) != plan.config_snapshot.get(key):
            config.set(key, plan.config_new[key], auto_save=False)
    if not config.save():
        raise RenameError('保存启动器配置失败')


def _restore_config(plan: RenamePlan):
    config = plan.config
    for key, value in plan.config_snapshot.items():
        config.set(key, value, auto_save=False)
    if (plan.config_existed or plan.config_changed) and not config.save():
        raise RenameError('恢复启动器配置失败')


def _rollback(plan: RenamePlan, jar_moved: bool) -> tuple[bool, str]:
    errors = []
    try:
        _restore_config(plan)
    except Exception as exc:
        errors.append(f'配置回滚失败：{exc}')
    try:
        if plan.profile_path is not None:
            if plan.profile_bytes is not None:
                _atomic_write(plan.profile_path, plan.profile_bytes)
            elif plan.profile_path.exists():
                plan.profile_path.unlink(missing_ok=True)
    except Exception as exc:
        errors.append(f'profile 回滚失败：{exc}')
    for path, original, _new in plan.children:
        try:
            _atomic_write(path, original)
        except Exception as exc:
            errors.append(f'子版本回滚失败 {path}：{exc}')
    try:
        base = plan.new_dir if plan.new_dir.exists() else plan.old_dir
        _atomic_write(base / plan.main_name, plan.main_bytes)
        new_main = base / plan.new_main_name
        if new_main != base / plan.main_name:
            new_main.unlink(missing_ok=True)
    except Exception as exc:
        errors.append(f'版本 JSON 回滚失败：{exc}')
    try:
        base = plan.new_dir if plan.new_dir.exists() else plan.old_dir
        if jar_moved:
            source = base / f'{plan.new_id}.jar'
            dest = base / f'{plan.old_id}.jar'
            if source.exists() and not dest.exists():
                os.replace(source, dest)
    except Exception as exc:
        errors.append(f'jar 回滚失败：{exc}')
    try:
        if plan.new_dir.exists() and not plan.old_dir.exists():
            plan.new_dir.rename(plan.old_dir)
        elif plan.new_dir.exists() and plan.old_dir.exists():
            errors.append('新旧版本目录同时存在，无法自动回滚')
    except Exception as exc:
        errors.append(f'版本目录回滚失败：{exc}')
    return not errors, '；'.join(errors)


def execute_rename(plan: RenamePlan, *, fail_after=None, crash_after=None):
    jar_moved = False
    try:
        _write_backups(plan)
        _write_journal(plan, 'prepared')
        plan.old_dir.rename(plan.new_dir)
        _write_journal(plan, 'dir_moved')
        if fail_after == 'dir_moved':
            raise RenameError('模拟失败：dir_moved')
        if crash_after == 'dir_moved':
            raise _SimulatedCrash('模拟崩溃：dir_moved')

        new_main = plan.new_dir / plan.new_main_name
        _atomic_write(new_main, plan.main_new_bytes)
        old_main = plan.new_dir / plan.main_name
        if old_main != new_main:
            old_main.unlink(missing_ok=True)
        _write_journal(plan, 'main_json')

        old_jar = plan.new_dir / f'{plan.old_id}.jar'
        new_jar = plan.new_dir / f'{plan.new_id}.jar'
        if old_jar.is_file() and not new_jar.exists():
            os.replace(old_jar, new_jar)
            jar_moved = True
            _write_journal(plan, 'jar', jar_moved)

        for index, (path, _original, new_bytes) in enumerate(plan.children):
            _atomic_write(path, new_bytes)
            _write_journal(plan, f'child_{index}')
            if fail_after == f'child_{index}':
                raise RenameError(f'模拟失败：child_{index}')
            if crash_after == f'child_{index}':
                raise _SimulatedCrash(f'模拟崩溃：child_{index}')

        if plan.profile_new_bytes is not None:
            _atomic_write(plan.profile_path, plan.profile_new_bytes)
            _write_journal(plan, 'profile')
            if fail_after == 'profile':
                raise RenameError('模拟失败：profile')
            if crash_after == 'profile':
                raise _SimulatedCrash('模拟崩溃：profile')

        _apply_config(plan)
        _write_journal(plan, 'config')
        if fail_after == 'config':
            raise RenameError('模拟失败：config')
        if crash_after == 'config':
            raise _SimulatedCrash('模拟崩溃：config')

        _write_journal(plan, 'committed', jar_moved)
        plan.journal_path.unlink(missing_ok=True)
        safe_rmtree(plan.backup_dir, ignore_errors=True)
        return plan.new_dir
    except _SimulatedCrash:
        raise
    except Exception as exc:
        ok, detail = _rollback(plan, jar_moved)
        if ok:
            plan.journal_path.unlink(missing_ok=True)
            safe_rmtree(plan.backup_dir, ignore_errors=True)
            raise RenameError(f'重命名失败，已回滚：{exc}') from exc
        raise RenameError(
            f'重命名失败，自动回滚不完整：{detail}；备份保留在 {plan.backup_dir}'
        ) from exc


def recover_pending_version_rename(mc_dir) -> str:
    versions_dir = Path(mc_dir) / 'versions'
    journal_path = versions_dir / JOURNAL_NAME
    if not journal_path.is_file():
        return ''
    try:
        data = _read_json(journal_path)
    except RenameError as exc:
        return f'重命名 journal 损坏：{exc}'
    if not isinstance(data, dict):
        return '重命名 journal 格式不正确'
    if str(data.get('phase') or '') == 'committed':
        journal_path.unlink(missing_ok=True)
        safe_rmtree(Path(str(data.get('backup_dir') or '')), ignore_errors=True)
        return ''
    backup_dir = Path(str(data.get('backup_dir') or ''))
    if not backup_dir.is_dir():
        return f'重命名回滚备份缺失：{backup_dir}'
    errors = []
    old_dir = Path(str(data.get('old_dir') or ''))
    new_dir = Path(str(data.get('new_dir') or ''))
    main_name = str(data.get('main_name') or '')
    new_main_name = str(data.get('new_main_name') or '')

    config_path = str(data.get('config_path') or '')
    config_backup = backup_dir / str(data.get('config_backup') or '')
    config_existed = bool(data.get('config_existed'))
    if config_path and config_backup.is_file():
        try:
            restored = json.loads(config_backup.read_text(encoding='utf-8'))
            from core.config import ConfigManager
            config = ConfigManager()
            for key in CONFIG_KEYS:
                if key in restored:
                    config.set(key, restored[key], auto_save=False)
            if not config.save():
                errors.append('配置恢复失败')
        except Exception as exc:
            errors.append(f'配置恢复失败：{exc}')
    elif config_path and not config_existed:
        try:
            Path(config_path).unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f'配置删除失败：{exc}')
    elif config_path and config_existed and not config_backup.is_file():
        errors.append('配置备份缺失')
    profile_path = str(data.get('profile_path') or '')
    profile_backup = backup_dir / str(data.get('profile_backup') or '')
    if profile_path:
        try:
            if profile_backup.is_file():
                _atomic_write(Path(profile_path), profile_backup.read_bytes())
            elif not data.get('profile_backup'):
                Path(profile_path).unlink(missing_ok=True)
            else:
                errors.append('profile 备份缺失')
        except Exception as exc:
            errors.append(f'profile 恢复失败：{exc}')
    for item in data.get('children') or []:
        try:
            path = Path(str(item.get('path') or ''))
            backup = backup_dir / str(item.get('backup') or '')
            if str(path) and backup.is_file():
                _atomic_write(path, backup.read_bytes())
            elif str(path):
                errors.append(f'子版本备份缺失：{path}')
        except Exception as exc:
            errors.append(f'子版本恢复失败：{exc}')
    base = new_dir if new_dir.exists() else old_dir
    try:
        main_backup = backup_dir / 'main.json.orig'
        if main_backup.is_file() and main_name:
            _atomic_write(base / main_name, main_backup.read_bytes())
        elif not main_backup.is_file():
            errors.append('主版本 JSON 备份缺失')
        if new_main_name and new_main_name != main_name:
            (base / new_main_name).unlink(missing_ok=True)
    except Exception as exc:
        errors.append(f'版本 JSON 恢复失败：{exc}')
    try:
        if data.get('jar_moved'):
            source = base / str(data.get('jar_new_name') or '')
            dest = base / str(data.get('jar_old_name') or '')
            if source.is_file() and not dest.exists():
                os.replace(source, dest)
    except Exception as exc:
        errors.append(f'jar 恢复失败：{exc}')
    try:
        if new_dir.exists() and not old_dir.exists():
            new_dir.rename(old_dir)
        elif new_dir.exists() and old_dir.exists():
            errors.append('新旧版本目录同时存在')
    except Exception as exc:
        errors.append(f'目录恢复失败：{exc}')
    if errors:
        return '重命名恢复不完整：' + '；'.join(errors)
    journal_path.unlink(missing_ok=True)
    safe_rmtree(backup_dir, ignore_errors=True)
    return ''


__all__ = [
    'JOURNAL_NAME',
    'RenameError',
    'RenamePlan',
    'build_rename_plan',
    'execute_rename',
    'recover_pending_version_rename',
]
