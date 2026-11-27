'''Minecraft Java 版本要求表。

每条记录: (适用的 MC 版本, 最低 JDK, 最高 JDK, 推荐 JDK, 备注)
最高版本用于提示“Java 过高”的兼容性问题，不代表一定无法启动。
'''

from __future__ import annotations
import re
from typing import Optional, Tuple
JAVA_REQUIREMENTS = [('1.0 ~ 1.16.5', 8, 8, 8, '老 Forge（1.12.2 等）很多不能跑高于 JDK 8，踩坑重灾区'), ('1.17 / 1.17.1', 16, 17, 17, 'JDK 16 非 LTS，短期过渡；推荐直接使用 JDK 17'), ('1.18 ~ 1.20.4', 17, 21, 17, '1.19.2、1.20.1 主流整合包都这一档'), ('1.20.5 ~ 1.21.11', 21, 25, 21, '1.21 全系列、新版 Fabric/NeoForge，推荐 JDK 21'), ('26.1+（新版号系列）', 25, None, 25, '2026 新大版本要求至少 JDK 25；JDK 26/27 等更新版本可继续使用')]

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
    max_text = '无上限' if max_major is None else f'JDK {max_major}'
    return {'label': label, 'min': f'JDK {min_major}', 'max': max_text, 'recommended': f'JDK {recommended_major}', 'min_major': min_major, 'max_major': max_major, 'recommended_major': recommended_major, 'note': note}

def java_major_bounds(version_name):
    '''返回 (最低 JDK 主版本, 最高 JDK 主版本)，无法识别时返回 None。'''

    parsed = parse_mc_version(version_name)
    if parsed is None:
        return
    return java_requirement_bounds(version_name)

def recommended_java_major(version_name):
    row = _requirement_row(version_name)
    if row is None:
        return
    return int(row[3])


def _loader_version_tuple(text):
    return tuple(int(part) for part in re.findall(r'\d+', str(text or ''))[:4])


def _mc_at_least(version, target):
    return version is not None and version >= target


def _cap(max_major, value):
    if value is None:
        return max_major
    return value if max_major is None else min(max_major, value)


def java_requirement_bounds(
    mc_version, loader='', loader_version='', release_time=None,
    has_optifine=False, java_major=None,
):
    '''返回 ``(最低 JDK, 最高 JDK 或 None)``，参考 PCL 的 Java 兼容矩阵。

    基础版本决定默认上下限；加载器/加载器版本和 OptiFine 再收窄范围。
    '''

    parsed = parse_mc_version(mc_version)
    if parsed is None:
        return (8, None)
    major, minor, patch = parsed
    version = (major, minor, patch)
    loader = str(loader or '').strip().lower()
    loader_version = str(loader_version or '').strip()
    lv = _loader_version_tuple(loader_version)

    if major >= 26:
        # 26+ 优先使用版本 JSON；缺少 JSON 时才按 PCL 的最低 25 处理。
        try:
            low = int(java_major) if java_major else 25
        except (TypeError, ValueError):
            low = 25
        return max(1, low), None

    if version >= (1, 20, 5):
        low, high = 21, 25
    elif version >= (1, 18, 0):
        low, high = 17, 21
    elif version >= (1, 17, 0):
        low, high = 16, 17
    elif version >= (1, 12, 0):
        low, high = 8, 8
    else:
        low, high = 8, 8

    try:
        json_major = int(java_major) if java_major else 0
    except (TypeError, ValueError):
        json_major = 0
    if json_major:
        low = max(low, json_major)

    has_optifine = bool(has_optifine) or loader == 'optifine'

    if loader == 'forge':
        if (1, 6, 1) <= version <= (1, 7, 2):
            return 7, 7
        if version <= (1, 12, 2):
            high = _cap(high, 8)
        elif (1, 13, 0) <= version < (1, 15, 0):
            high = _cap(high, 10)
        elif (1, 15, 0) <= version < (1, 16, 0):
            high = _cap(high, 15)

        if (
            (1, 16, 3) <= version <= (1, 16, 5)
            and lv and (34, 0, 0) <= lv <= (36, 2, 25)
        ):
            high = _cap(high, 8)
        if (
            version == (1, 16, 5)
            and lv and (36, 2, 26) <= lv < (37, 0, 0)
        ):
            high = 23
        if (
            version == (1, 17, 1)
            and lv and (37, 0, 0) <= lv <= (37, 0, 79)
        ):
            high = _cap(high, 16)
        if version >= (1, 18, 0) and has_optifine:
            high = _cap(high, 18)
        if (
            version == (1, 19, 4)
            and lv and (45, 0, 21) <= lv <= (45, 0, 65)
        ):
            high = _cap(high, 19)
        if (
            (1, 19, 4) <= version <= (1, 20, 1)
            and lv and (45, 0, 66) <= lv <= (47, 4, 8)
        ):
            high = _cap(high, 21)

    elif loader == 'neoforge':
        if version == (1, 20, 1):
            high = _cap(high, 21)
        elif version == (1, 20, 2) and lv and lv < (20, 2, 62):
            high = _cap(high, 21)

    elif loader == 'fabric':
        if (1, 15, 0) <= version <= (1, 16, 5):
            low = max(low, 8)
        elif version >= (1, 18, 0):
            low = max(low, 17)
        if lv and lv < (0, 17, 0):
            # 老 Fabric Loader 可兼容到 Java 24，不能被基础表的 21 提前截断。
            high = 24
        elif lv and lv >= (0, 17, 0):
            high = None

    elif loader == 'optifine':
        if version < (1, 7, 0):
            high = _cap(high, 8)
        elif (1, 8, 0) <= version <= (1, 11, 2):
            return 8, 8
        elif version == (1, 12, 0) or version == (1, 12, 1) or version == (1, 12, 2):
            high = _cap(high, 8)

    elif loader == 'liteloader':
        high = _cap(high, 8)

    if has_optifine and loader != 'optifine':
        if version < (1, 7, 0):
            high = _cap(high, 8)
        elif (1, 8, 0) <= version <= (1, 11, 2):
            high = _cap(high, 8)
        elif version <= (1, 12, 2):
            high = _cap(high, 8)

    if high is not None and low > high:
        low = high
    return int(low), (None if high is None else int(high))

