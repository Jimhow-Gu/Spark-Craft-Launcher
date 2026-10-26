'''与 PCL2 / HMCL 的版本设置互通。

互通内容：版本隔离（版本独立游戏目录）。
- PCL2：versions/<id>/PCL/settings.ini → [其他] VersionIsolation
- HMCL：versions/<id>/hmclversion.cfg → useRelativePath

我们自己的隔离状态仍以 config 的 version_settings 为准；
外部标记只在没有本地设置时生效，修改隔离开关时会同步写回两边。
'''

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger('Interop')

_TRUE = ('true', '1', 'yes', 'on')


def _read_key_value_file(path):
    '''读取 INI/properties 风格文件，返回 {lower(key): value}（含所有 section 的键）。'''

    result = {}
    try:
        text = Path(path).read_text(encoding='utf-8-sig', errors='replace')
    except (OSError, ValueError):
        return result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith((';', '#', '//')):
            continue
        if line.startswith('[') and line.endswith(']'):
            continue
        for sep in ('=', ':'):
            if sep in line:
                key, _, value = line.partition(sep)
                result[key.strip().lower()] = value.strip()
                break
    return result


def _update_key_value_file(path, updates, section=None):
    '''就地更新键值文件（匹配不区分大小写，保留其余内容）。

    指定 section 时：键写到该节内（节不存在则创建）；
    不指定时按 properties 风格直接追加到末尾。
    '''

    path = Path(path)
    lines = []
    if path.is_file():
        try:
            lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
        except (OSError, ValueError):
            lines = []
    pending = {key.lower(): (key, value) for key, value in updates.items()}
    out = []
    section_header = f'[{section}]' if section else None
    insert_at = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            if (section
                    and stripped[1:-1].strip().lower() == str(section).strip().lower()
                    and insert_at is None):
                insert_at = len(out) + 1
            out.append(line)
            continue
        key = None
        sep_used = None
        if stripped and not stripped.startswith((';', '#', '//')):
            for sep in ('=', ':'):
                if sep in stripped:
                    key = stripped.partition(sep)[0].strip().lower()
                    sep_used = sep
                    break
        if key is not None and key in pending:
            _orig, value = pending.pop(key)
            file_key = stripped.partition(sep_used)[0].strip()
            out.append(f'{file_key}={value}')
        else:
            out.append(line)
    if pending:
        new_lines = [f'{key}={value}' for _lower, (key, value) in pending.items()]
        if section and insert_at is not None:
            out = out[:insert_at] + new_lines + out[insert_at:]
        elif section:
            if out and out[-1].strip():
                out.append('')
            out.append(section_header)
            out.extend(new_lines)
        else:
            out.extend(new_lines)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(out) + '\n', encoding='utf-8')
        return True
    except OSError:
        return False


def read_external_isolation(version_dir):
    '''读取 PCL/HMCL 标记的版本隔离状态；返回 None 表示没有外部标记。'''

    version_dir = Path(version_dir)
    for path in (
        version_dir / 'PCL' / 'settings.ini',
        version_dir / 'hmclversion.cfg',
    ):
        data = _read_key_value_file(path)
        for key in ('versionisolation', 'userelativepath'):
            if key in data:
                return str(data[key]).strip().lower() in _TRUE
    return None


def write_isolation_markers(version_dir, isolated: bool):
    '''把隔离状态写进 PCL/HMCL 的版本设置文件（不存在则创建）。'''

    version_dir = Path(version_dir)
    value = 'True' if isolated else 'False'
    ok_pcl = _update_key_value_file(
        version_dir / 'PCL' / 'settings.ini',
        {'VersionIsolation': value},
        section='其他',
    )
    ok_hmcl = _update_key_value_file(
        version_dir / 'hmclversion.cfg',
        {'useRelativePath': 'true' if isolated else 'false'},
    )
    if not (ok_pcl and ok_hmcl):
        logger.warning('写入 PCL/HMCL 隔离标记失败: %s', version_dir)
    return ok_pcl and ok_hmcl
