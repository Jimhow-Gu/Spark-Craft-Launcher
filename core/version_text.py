"""版本日期解析与显示。所有日期格式集中在这里。"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_release_datetime(version):
    """返回本地时区的 releaseTime/time；缺失或非法时返回 None。"""

    if not isinstance(version, dict):
        return None
    raw = str(
        version.get('releaseTime') or version.get('time') or ''
    ).strip()
    if not raw:
        return None
    if raw.endswith(('Z', 'z')):
        raw = raw[:-1] + '+00:00'
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone()


def format_release_time(version):
    value = parse_release_datetime(version)
    if value is None:
        return '发布日期未知'
    return value.strftime('%Y/%m/%d %H:%M')


def release_year(version):
    value = parse_release_datetime(version)
    return str(value.year) if value is not None else ''


def is_april_first(version):
    value = parse_release_datetime(version)
    if value is not None:
        return (value.month, value.day) == (4, 1)
    raw = str(
        version.get('releaseTime') or version.get('time') or ''
    )
    return raw[5:10] == '04-01'


__all__ = [
    'format_release_time', 'is_april_first', 'parse_release_datetime',
    'release_year',
]
