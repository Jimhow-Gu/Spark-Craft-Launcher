'''与 PCL2 / HMCL 的版本设置互通。

互通内容：版本隔离（版本独立游戏目录）。
- PCL2：versions/<id>/PCL/Setup.ini → VersionArgumentIndieV2
        旧版 settings.ini → VersionIsolation
- HMCL：versions/<id>/hmclversion.cfg → gameDirType / useRelativePath（JSON）
        versions/<id>/.hmcl/config/instance-game-settings.json
        → runningDirectory / overrideProperties

我们自己的隔离状态仍以 config 的 version_settings 为准；
外部标记只在没有本地设置时生效，修改隔离开关时会同步写回两边。
'''

from __future__ import annotations

import codecs
import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger('Interop')

_TRUE = ('true', '1', 'yes', 'on')
_FALSE = ('false', '0', 'no', 'off')


def _read_text(path):
    '''读取 PCL/HMCL 配置，兼容 UTF-8、UTF-16 和旧版 GBK。'''

    try:
        data = Path(path).read_bytes()
    except (OSError, ValueError):
        return ''
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ('utf-16',)
    else:
        encodings = ('utf-8-sig', 'gbk', 'latin-1')
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('utf-8', errors='replace')


def _write_text_atomic(path, text):
    '''原子写入，避免启动器退出/崩溃时留下半个配置文件。'''

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=path.name + '.', suffix='.tmp', dir=str(path.parent)
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        os.replace(temp_path, path)
        return True
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _read_key_value_file(path):
    '''读取 INI/properties 风格文件，返回 {lower(key): value}（含所有 section 的键）。'''

    result = {}
    text = _read_text(path)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith((';', '#', '//')):
            continue
        if line.startswith('[') and line.endswith(']'):
            continue
        positions = [(line.find(sep), sep) for sep in ('=', ':') if sep in line]
        if not positions:
            continue
        index, sep = min(positions, key=lambda item: item[0])
        if index <= 0:
            continue
        key, _, value = line.partition(sep)
        result[key.strip().lower()] = value.strip()
    return result


def _update_key_value_file(path, updates, section=None, separator='='):
    '''就地更新键值文件（匹配不区分大小写，保留其余内容）。'''

    path = Path(path)
    lines = []
    if path.is_file():
        lines = _read_text(path).splitlines()
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
            positions = [
                (stripped.find(sep), sep)
                for sep in ('=', ':') if sep in stripped
            ]
            if positions:
                index, sep_used = min(positions, key=lambda item: item[0])
                if index > 0:
                    key = stripped[:index].strip().lower()
        if key is not None and key in pending:
            _orig, value = pending.pop(key)
            file_key = stripped.partition(sep_used)[0].strip()
            out.append(f'{file_key}{sep_used}{value}')
        else:
            out.append(line)
    if pending:
        new_lines = [
            f'{key}{separator}{value}'
            for _lower, (key, value) in pending.items()
        ]
        if section and insert_at is not None:
            out = out[:insert_at] + new_lines + out[insert_at:]
        elif section:
            if out and out[-1].strip():
                out.append('')
            out.append(section_header)
            out.extend(new_lines)
        else:
            out.extend(new_lines)
    return _write_text_atomic(path, '\n'.join(out) + '\n')


def _read_json_file(path):
    text = _read_text(path).strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_file(path, data):
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    except (TypeError, ValueError):
        return False
    return _write_text_atomic(path, text)


def _parse_bool(value):
    text = str(value or '').strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def _parse_int(value):
    try:
        return int(str(value or '').strip())
    except (TypeError, ValueError):
        return None


def _same_path(left, right):
    try:
        left_key = os.path.normcase(os.path.abspath(str(left)))
        right_key = os.path.normcase(os.path.abspath(str(right)))
    except (TypeError, ValueError, OSError):
        return False
    return left_key == right_key


def _hmcl_game_dir_isolation(value):
    '''HMCL 旧版 gameDirType：0=根目录，1=版本目录，2=自定义目录。'''

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        if number == 1:
            return True
        if number in (0, 2):
            return False
        return None
    text = str(value or '').strip().lower()
    if text in ('1', 'version_folder', 'versionfolder'):
        return True
    if text in ('0', '2', 'root_folder', 'rootfolder', 'custom'):
        return False
    return None


def _read_pcl_isolation(version_dir):
    setup_path = version_dir / 'PCL' / 'Setup.ini'
    if setup_path.is_file():
        data = _read_key_value_file(setup_path)
        if 'versionargumentindiev2' in data:
            parsed = _parse_bool(data['versionargumentindiev2'])
            if parsed is not None:
                return parsed
        if 'versionisolation' in data:
            parsed = _parse_bool(data['versionisolation'])
            if parsed is not None:
                return parsed
        if 'versionargumentindie' in data:
            legacy = _parse_int(data['versionargumentindie'])
            if legacy == 1:
                return True
            if legacy == 2:
                return False
    # 兼容本启动器旧版本写出的 settings.ini。
    legacy_path = version_dir / 'PCL' / 'settings.ini'
    if not setup_path.is_file() and legacy_path.is_file():
        data = _read_key_value_file(legacy_path)
        for key in ('versionisolation', 'versionargumentindiev2'):
            if key in data:
                parsed = _parse_bool(data[key])
                if parsed is not None:
                    return parsed
    return None


def _read_hmcl_legacy_isolation(version_dir):
    path = version_dir / 'hmclversion.cfg'
    if not path.is_file():
        return None
    text = _read_text(path).lstrip()
    if text.startswith(('{', '[')):
        data = _read_json_file(path)
        if data is None:
            return None
        lower = {str(key).lower(): value for key, value in data.items()}
        if 'gamedirtype' in lower:
            parsed = _hmcl_game_dir_isolation(lower['gamedirtype'])
            if parsed is not None:
                return parsed
        if 'userelativepath' in lower:
            return _parse_bool(lower['userelativepath'])
        return None
    # 兼容本启动器旧版本写出的 key=value 文件。
    data = _read_key_value_file(path)
    for key in ('userelativepath', 'versionisolation'):
        if key in data:
            parsed = _parse_bool(data[key])
            if parsed is not None:
                return parsed
    return None


def _read_hmcl_instance_isolation(version_dir):
    path = version_dir / '.hmcl' / 'config' / 'instance-game-settings.json'
    if not path.is_file():
        return None
    data = _read_json_file(path)
    if data is None:
        return None
    overrides = data.get('overrideProperties')
    if isinstance(overrides, list):
        if not any(str(item) == 'runningDirectory' for item in overrides):
            return None
    elif 'runningDirectory' not in data:
        return None
    value = str(data.get('runningDirectory') or '').strip()
    if not value:
        # HMCL：runningDirectory 被覆写且为空时使用版本文件夹。
        return True
    try:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = version_dir / candidate
        candidate = candidate.resolve(strict=False)
        version_root = version_dir.resolve(strict=False)
        game_root = version_dir.parent.parent.resolve(strict=False)
    except (OSError, ValueError):
        return False
    if _same_path(candidate, version_root):
        return True
    if _same_path(candidate, game_root):
        return False
    return False


def read_external_isolation(version_dir):
    '''读取 PCL/HMCL 的版本隔离状态；返回 None 表示没有外部标记。'''

    version_dir = Path(version_dir)
    try:
        pcl = _read_pcl_isolation(version_dir)
    except Exception as exc:
        logger.debug('读取 PCL 隔离设置失败: %s', exc)
        pcl = None
    if pcl is not None:
        return pcl
    try:
        # HMCL 对带 modpack.cfg 的整合包强制使用版本目录。
        if (version_dir / 'modpack.cfg').is_file():
            return True
        hmcl = _read_hmcl_legacy_isolation(version_dir)
        if hmcl is None:
            hmcl = _read_hmcl_instance_isolation(version_dir)
    except Exception as exc:
        logger.debug('读取 HMCL 隔离设置失败: %s', exc)
        hmcl = None
    return hmcl


def _write_pcl_isolation(version_dir, isolated):
    setup_path = version_dir / 'PCL' / 'Setup.ini'
    ok = _update_key_value_file(
        setup_path,
        {
            'VersionArgumentIndieV2': 'True' if isolated else 'False',
            'VersionArgumentIndie': '1' if isolated else '2',
        },
        separator=':',
    )
    legacy_path = version_dir / 'PCL' / 'settings.ini'
    if legacy_path.is_file():
        _update_key_value_file(
            legacy_path,
            {'VersionIsolation': 'True' if isolated else 'False'},
            section='其他',
            separator='=',
        )
    return ok


def _write_hmcl_legacy_isolation(version_dir, isolated):
    path = version_dir / 'hmclversion.cfg'
    if not path.is_file():
        return _write_json_file(
            path,
            {
                'gameDirType': 1 if isolated else 0,
                'useRelativePath': bool(isolated),
            },
        )
    text = _read_text(path).lstrip()
    if text.startswith(('{', '[')):
        data = _read_json_file(path)
        if data is None:
            logger.warning('HMCL 配置文件不是有效 JSON，跳过写入: %s', path)
            return False
        data['gameDirType'] = 1 if isolated else 0
        if 'useRelativePath' in data:
            data['useRelativePath'] = bool(isolated)
        return _write_json_file(path, data)
    data = _read_key_value_file(path)
    if set(data).issubset({'userelativepath', 'gamedirtype'}):
        return _write_json_file(
            path,
            {
                'gameDirType': 1 if isolated else 0,
                'useRelativePath': bool(isolated),
            },
        )
    return _update_key_value_file(
        path,
        {
            'useRelativePath': 'true' if isolated else 'false',
            'gameDirType': '1' if isolated else '0',
        },
        separator='=',
    )


def _write_hmcl_instance_isolation(version_dir, isolated):
    path = version_dir / '.hmcl' / 'config' / 'instance-game-settings.json'
    if not path.is_file():
        return True
    data = _read_json_file(path)
    if data is None:
        logger.warning('HMCL 实例配置不是有效 JSON，跳过写入: %s', path)
        return False
    overrides = data.get('overrideProperties')
    if not isinstance(overrides, list):
        overrides = []
    if 'runningDirectory' not in overrides:
        overrides.append('runningDirectory')
    data['overrideProperties'] = overrides
    data['runningDirectory'] = (
        '' if isolated else str(version_dir.parent.parent)
    )
    return _write_json_file(path, data)


def write_isolation_markers(version_dir, isolated: bool):
    '''把隔离状态写进 PCL/HMCL 的版本设置文件（不存在则创建）。'''

    version_dir = Path(version_dir)
    isolated = bool(isolated)
    try:
        ok_pcl = _write_pcl_isolation(version_dir, isolated)
    except Exception as exc:
        logger.warning('写入 PCL 隔离标记失败: %s', exc)
        ok_pcl = False
    try:
        ok_hmcl = _write_hmcl_legacy_isolation(version_dir, isolated)
        if ok_hmcl:
            ok_hmcl = _write_hmcl_instance_isolation(version_dir, isolated)
    except Exception as exc:
        logger.warning('写入 HMCL 隔离标记失败: %s', exc)
        ok_hmcl = False
    if not (ok_pcl and ok_hmcl):
        logger.warning('写入 PCL/HMCL 隔离标记失败: %s', version_dir)
    return ok_pcl and ok_hmcl
