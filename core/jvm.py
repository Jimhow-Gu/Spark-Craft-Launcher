'''JVM / 高级启动参数工具'''

import shlex
from typing import List
DEFAULT_JVM_ARGS = '-XX:-OmitStackTraceInFastThrow -Djdk.lang.Process.allowAmbiguousCommands=True -Dfml.ignoreInvalidMinecraftCertificates=True -Dfml.ignorePatchDiscrepancies=True'
MEMORY_GC_OPTIONS = [('auto', '自动（推荐）'), ('zgc', '尽量使用 ZGC'), ('zgc_generational', '尽量使用分代 ZGC'), ('g1', '标准 G1GC'), ('tuned_g1', '调优 G1GC'), ('none', '不指定（可自定义）')]
# 参考 PCL/HMCL 的稳定配置：目标低停顿，避免超大堆下频繁晋升和巨大 Young GC。
# 不再使用 MaxTenuringThreshold=1 / SurvivorRatio=32 这类会放大卡顿的参数。
_TUNED_G1 = [
    '-XX:+UnlockExperimentalVMOptions',
    '-XX:+UseG1GC',
    '-XX:G1NewSizePercent=20',
    '-XX:G1ReservePercent=20',
    '-XX:G1HeapRegionSize=32M',
    '-XX:MaxGCPauseMillis=50',
    '-XX:+ParallelRefProcEnabled',
    '-XX:+PerfDisableSharedMem',
]

def gc_arguments(key: str, java_major=None) -> List[str]:
    key = (key or 'auto').lower()
    try:
        major = int(java_major) if java_major is not None else None
    except (TypeError, ValueError):
        major = None
    if key == 'auto':
        # 自动模式优先使用吞吐更稳定的 G1。ZGC 虽然停顿低，但 Minecraft
        # 分配速率很高，实测同堆下 G1 的分配吞吐明显更高；需要低停顿时
        # 用户仍可在高级设置里手动选择 ZGC。
        return list(_TUNED_G1)
    if key == 'zgc':
        if major is not None and major < 15:
            return list(_TUNED_G1)
        return ['-XX:+UseZGC']
    if key == 'zgc_generational':
        if major is not None and major < 21:
            if major >= 15:
                return ['-XX:+UseZGC']
            return list(_TUNED_G1)
        if major is not None and major >= 23:
            # ZGenerational 在 Java 23+ 已成为默认，旧参数会被忽略并告警。
            return ['-XX:+UseZGC']
        return ['-XX:+UnlockExperimentalVMOptions', '-XX:+UseZGC', '-XX:+ZGenerational']
    if key == 'g1':
        return ['-XX:+UseG1GC', '-XX:MaxGCPauseMillis=50']
    if key == 'tuned_g1':
        return list(_TUNED_G1)
    return []

def split_arguments(text: str) -> List[str]:
    text = (text or '').strip()
    if not text:
        return []
    try:
        return [part for part in shlex.split(text, posix=False) if part]
    except Exception:
        return [part for part in text.split() if part]

