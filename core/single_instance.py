'''Windows 单实例锁；退出时可显式释放，避免残留进程导致“启动器已经启动”。'''

from __future__ import annotations

import ctypes
import sys

_HANDLE = None
_MUTEX_NAME = 'Local\\SparkCraftLauncher.SingleInstance.v1'


def acquire_single_instance() -> bool:
    global _HANDLE
    if sys.platform != 'win32':
        return True
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.restype = ctypes.c_void_p
        create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        handle = create_mutex(None, False, _MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        _HANDLE = handle
        return True
    except Exception:
        return True


def release_single_instance():
    '''退出/确认关闭时立即释放互斥锁，让新实例可以正常启动。'''

    global _HANDLE
    handle = _HANDLE
    _HANDLE = None
    if not handle or sys.platform != 'win32':
        return
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        pass
