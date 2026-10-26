'''JVM / 高级启动参数工具'''

import shlex
from typing import List
DEFAULT_JVM_ARGS = '-XX:-OmitStackTraceInFastThrow -Djdk.lang.Process.allowAmbiguousCommands=True -Dfml.ignoreInvalidMinecraftCertificates=True -Dfml.ignorePatchDiscrepancies=True'
MEMORY_GC_OPTIONS = [('zgc', '尽量使用 ZGC'), ('zgc_generational', '尽量使用分代 ZGC'), ('g1', '标准 G1GC'), ('tuned_g1', '调优 G1GC'), ('none', '不指定（可自定义）')]
_TUNED_G1 = ['-XX:+UnlockExperimentalVMOptions', '-XX:+UseG1GC', '-XX:G1NewSizePercent=30', '-XX:G1MaxNewSizePercent=40', '-XX:G1HeapRegionSize=8M', '-XX:G1ReservePercent=20', '-XX:G1HeapWastePercent=5', '-XX:G1MixedGCCountTarget=4', '-XX:InitiatingHeapOccupancyPercent=15', '-XX:G1MixedGCLiveThresholdPercent=90', '-XX:G1RSetUpdatingPauseTimePercent=5', '-XX:SurvivorRatio=32', '-XX:+PerfDisableSharedMem', '-XX:MaxTenuringThreshold=1']

def gc_arguments(key: str) -> List[str]:
    key = (key or 'tuned_g1').lower()
    if key == 'zgc':
        return ['-XX:+UseZGC']
    if key == 'zgc_generational':
        return ['-XX:+UseZGC', '-XX:+ZGenerational']
    if key == 'g1':
        return ['-XX:+UseG1GC']
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

