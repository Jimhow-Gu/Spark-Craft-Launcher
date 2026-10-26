'''Minecraft Java 版本要求表。

每条记录: (适用的 MC 版本, 最低 JDK, 最高 JDK, 推荐 JDK, 备注)
最高版本用于提示“Java 过高”的兼容性问题，不代表一定无法启动。
'''

from __future__ import annotations
import re
from typing import Optional, Tuple
JAVA_REQUIREMENTS = [('1.0 ~ 1.16.5', 8, 8, 8, '老 Forge（1.12.2 等）很多不能跑高于 JDK 8，踩坑重灾区'), ('1.17 / 1.17.1', 16, 17, 17, 'JDK 16 非 LTS，短期过渡；推荐直接使用 JDK 17'), ('1.18 ~ 1.20.4', 17, 21, 17, '1.19.2、1.20.1 主流整合包都这一档'), ('1.20.5 ~ 1.21.11', 21, 25, 21, '1.21 全系列、新版 Fabric/NeoForge，推荐 JDK 21'), ('26.1+（新版号系列）', 25, 25, 25, '2026 新大版本，快照 + 正式版统一要求 25')]

def parse_mc_version(version_name):
    if not version_name:
        return
    text = str(version_name)
    match = re.search('(?<!\\d)(\\d+)\\.(\\d+)(?:\\.(\\d+))?', text)
    if not match:
        return
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)

def _requirement_row(version_name):
    parsed = parse_mc_version(version_name)
    if parsed is None:
        return
    major, minor, patch = parsed
    if major >= 26:
        return JAVA_REQUIREMENTS[4]
    if major == 1 and (minor, patch) <= (16, 5):
        return JAVA_REQUIREMENTS[0]
    if major == 1 and minor == 17:
        return JAVA_REQUIREMENTS[1]
    if major == 1 and (minor, patch) <= (20, 4):
        return JAVA_REQUIREMENTS[2]
    if major == 1 and (minor, patch) <= (21, 11):
        return JAVA_REQUIREMENTS[3]

def java_requirement(version_name):
    row = _requirement_row(version_name)
    if row is None:
        return
    label, min_major, max_major, recommended_major, note = row
    return {'label': label, 'min': f'JDK {min_major}', 'max': f'JDK {max_major}', 'recommended': f'JDK {recommended_major}', 'min_major': min_major, 'max_major': max_major, 'recommended_major': recommended_major, 'note': note}

def java_major_bounds(version_name):
    '''返回 (最低 JDK 主版本, 最高 JDK 主版本)，无法识别时返回 None。'''

    row = _requirement_row(version_name)
    if row is None:
        return
    return int(row[1]), int(row[2])

def recommended_java_major(version_name):
    row = _requirement_row(version_name)
    if row is None:
        return
    return int(row[3])

