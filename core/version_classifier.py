"""Minecraft 版本分类：正式版、快照、预发布、愚人节、远古版。"""

from __future__ import annotations

import re
from collections import OrderedDict

from core.version_lore import fool_registry
from core.version_text import is_april_first

CATEGORIES = (
    ('all', '全部'),
    ('release', '正式版'),
    ('snapshot', '快照版'),
    ('preview', '预发布版'),
    ('fool', '愚人节版'),
    ('old', '远古版'),
)
CATEGORY_LABELS = dict(CATEGORIES)
GROUP_ORDER = ('release', 'snapshot', 'preview', 'fool', 'old')

_W_SNAPSHOT = re.compile(r'^\d{2}w\d{2}[a-z]$', re.I)


def _text(value):
    return str(value or '').strip()


def classify_version(version):
    """返回 release/snapshot/preview/fool/old 之一。"""

    if not isinstance(version, dict):
        return 'snapshot'
    version_id = _text(version.get('id'))
    lower = version_id.lower()
    version_type = _text(version.get('type')).lower()
    release_time = _text(
        version.get('releaseTime') or version.get('time')
    )
    if (
        fool_registry.is_fool_id(lower)
        or is_april_first(version)
    ):
        return 'fool'
    year_match = re.match(r'^(\d{4})-', release_time)
    year = int(year_match.group(1)) if year_match else 0
    if version_type in ('old_alpha', 'old_beta') or 2001 <= year <= 2012:
        return 'old'
    if re.search(r'(?:^|[-_.])(?:pre|rc)\d*', lower):
        return 'preview'
    if (
        version_type == 'snapshot'
        or 'snapshot' in lower
        or _W_SNAPSHOT.match(version_id)
    ):
        return 'snapshot'
    if version_type == 'release':
        return 'release'
    return 'snapshot'


def grouped_versions(versions):
    """按分类分组，组内保持 releaseTime 倒序。"""

    groups = OrderedDict((key, []) for key in GROUP_ORDER)
    for version in versions or ():
        if isinstance(version, dict):
            groups[classify_version(version)].append(version)
    return groups


def category_counts(versions):
    groups = grouped_versions(versions)
    return {key: len(groups.get(key) or ()) for key in GROUP_ORDER}


__all__ = [
    'CATEGORIES', 'CATEGORY_LABELS', 'GROUP_ORDER',
    'category_counts', 'classify_version', 'grouped_versions',
]
