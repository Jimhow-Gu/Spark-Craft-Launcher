'''动态内存分配：参考 PCL 的 GetRam 分阶段算法，按可用内存与 Mod 数量计算。'''

from __future__ import annotations

import ctypes
from pathlib import Path

MOD_EXTENSIONS = {'.jar', '.zip', '.litemod'}
MOD_LOADERS = {'forge', 'neoforge', 'fabric', 'quilt', 'cleanroom'}


def memory_info_gb():
    '''返回 (总物理内存 GB, 可用物理内存 GB)。'''

    try:
        import psutil
        mem = psutil.virtual_memory()
        return mem.total / 1073741824.0, mem.available / 1073741824.0
    except Exception:
        pass
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ('dwLength', ctypes.c_ulong),
                ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong),
                ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong),
                ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong),
                ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
            ]
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys / 1073741824.0, status.ullAvailPhys / 1073741824.0
    except Exception:
        pass
    return 8.0, 4.0


def count_mods(mc_dir, version_id=''):
    base = Path(mc_dir)
    candidates = []
    if version_id:
        candidates.append(base / 'versions' / str(version_id) / 'mods')
    candidates.append(base / 'mods')
    for directory in candidates:
        try:
            if not directory.is_dir():
                continue
            return sum(1 for path in directory.iterdir() if path.is_file() and path.suffix.lower() in MOD_EXTENSIONS)
        except OSError:
            continue
    return 0


def auto_memory_gb(available_gb, mod_count, *, modable=False, optifine=False):
    '''PCL GetRam：阶段一 100%、阶段二 70%、阶段三 40%、阶段四 15%。'''

    available = max(0.0, float(available_gb))
    mods = max(0, int(mod_count))
    if modable:
        minimum = 0.5 + mods / 150.0
        target1 = 1.5 + mods / 90.0
        target2 = 2.7 + mods / 50.0
        target3 = 4.5 + mods / 25.0
    elif optifine:
        minimum, target1, target2, target3 = 0.5, 1.5, 3.0, 5.0
    else:
        minimum, target1, target2, target3 = 0.5, 1.5, 2.5, 4.0
    give = 0.0
    for delta, ratio in (
        (target1, 1.0),
        (target2 - target1, 0.7),
        (target3 - target2, 0.4),
        (target3, 0.15),
    ):
        give += min(available * ratio, delta)
        available -= delta / ratio
        if available < 0.1:
            break
    return max(give, minimum)


def recommended_ram_mb(mc_dir, version_id='', loader='', mod_count=None):
    total_gb, available_gb = memory_info_gb()
    if mod_count is None:
        mod_count = count_mods(mc_dir, version_id)
    loader = str(loader or '').lower()
    modable = bool(version_id) and (bool(mod_count) or loader in MOD_LOADERS)
    optifine = loader == 'optifine' or (not loader and 'optifine' in str(version_id).lower())
    give_gb = auto_memory_gb(available_gb, mod_count, modable=modable, optifine=optifine)
    give_gb = min(give_gb, max(0.5, total_gb - 0.5), 32.0)
    mb = int(round(give_gb * 1024))
    return max(512, mb)


__all__ = ['auto_memory_gb', 'count_mods', 'memory_info_gb', 'recommended_ram_mb']
