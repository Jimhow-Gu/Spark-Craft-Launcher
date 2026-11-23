"""Windows 任务栏进度条封装（ITaskbarList3）。

放置位置建议：``core/taskbar_progress.py``。

用法：
    from core.taskbar_progress import taskbar_progress

    # 主窗口 showEvent 之后调用一次
    taskbar_progress.attach(self)

    # 0-100 的确定进度；percent=None 表示不确定进度
    taskbar_progress.set_task("download", 42)
    taskbar_progress.set_task("launch", None)

    # 任务结束/取消/失败时清除
    taskbar_progress.clear_task("download")

多个任务同时存在时，按 launch > import > download > repair 的优先级显示，
避免下载进度覆盖启动准备或整合包导入进度。
"""

from __future__ import annotations

import ctypes
import sys
import time
import uuid
from ctypes import wintypes
from typing import Dict, Optional, Tuple

_IS_WINDOWS = sys.platform == "win32"

# ITaskbarList3 状态
_TBPF_NOPROGRESS = 0
_TBPF_INDETERMINATE = 1
_TBPF_NORMAL = 2
_TBPF_ERROR = 4
_TBPF_PAUSED = 8

# 同一时刻只显示一个任务；数值越小优先级越高
_TASK_PRIORITY = ("launch", "import", "download", "repair")


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(value: str) -> _GUID:
    item = uuid.UUID(value)
    return _GUID(
        item.time_low,
        item.time_mid,
        item.time_hi_version,
        (ctypes.c_ubyte * 8)(*item.bytes[8:]),
    )


class TaskbarProgress:
    """轻量级 ITaskbarList3 包装，只在 Windows 上生效。"""

    _CLSID_TASKBAR_LIST = "56FDF344-FD6D-11d0-958A-006097C9A090"
    _IID_ITASKBAR_LIST3 = "EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF"

    def __init__(self) -> None:
        self._hwnd: Optional[int] = None
        self._ptr: Optional[ctypes.c_void_p] = None
        self._tasks: Dict[str, Tuple[str, Optional[float]]] = {}
        self._last_value: Optional[Tuple[int, int]] = None
        self._last_state: Optional[int] = None
        self.last_error: str = ""

    @property
    def available(self) -> bool:
        return bool(_IS_WINDOWS and self._hwnd and self._ptr)

    def attach(self, window) -> bool:
        """绑定主窗口。必须在窗口已经创建原生句柄后调用。"""
        if not _IS_WINDOWS:
            return False
        try:
            hwnd = int(window.winId())
        except Exception as exc:  # pragma: no cover - 仅 Windows 分支
            self.last_error = f"winId: {exc}"
            return False
        if not hwnd:
            self.last_error = "winId is empty"
            return False

        self._hwnd = hwnd
        if self._ptr is None:
            self._create_taskbar()
        self._flush(force=True)
        return self.available

    def set_task(
        self,
        key: str,
        percent: Optional[float] = None,
        state: str = "normal",
    ) -> None:
        """设置某个任务的进度。

        percent=None 时显示不确定进度；state 可为 normal/error/paused。
        """
        normalized: Optional[float]
        if percent is None:
            normalized = None
        else:
            try:
                normalized = max(0.0, min(100.0, float(percent)))
            except (TypeError, ValueError):
                normalized = None

        self._tasks[key] = (state, normalized)
        self._flush()

    def clear_task(self, key: str) -> None:
        if key in self._tasks:
            self._tasks.pop(key, None)
            self._flush(force=True)

    def sync_download(self, snapshot: dict) -> None:
        """根据 DownloadManager.snapshot() 同步下载任务进度。"""
        if not snapshot or not snapshot.get("active"):
            self.clear_task("download")
            return
        state = "error" if snapshot.get("failed") else "normal"
        self.set_task("download", snapshot.get("percent", 0), state)

    def shutdown(self) -> None:
        """窗口关闭时清除任务栏进度并释放 COM 对象。"""
        if not _IS_WINDOWS:
            return
        self._tasks.clear()
        if self._ptr is not None:
            self._set_state(_TBPF_NOPROGRESS)
            self._release()
            self._ptr = None
        self._hwnd = None
        self._last_value = None
        self._last_state = None

    # ------------------------------------------------------------------
    # COM 实现
    # ------------------------------------------------------------------
    def _create_taskbar(self) -> None:
        try:
            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)
            ole32.CoCreateInstance.argtypes = [
                ctypes.POINTER(_GUID),
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            ole32.CoCreateInstance.restype = ctypes.c_long

            clsid = _guid(self._CLSID_TASKBAR_LIST)
            iid = _guid(self._IID_ITASKBAR_LIST3)
            ptr = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(clsid),
                None,
                1,  # CLSCTX_INPROC_SERVER
                ctypes.byref(iid),
                ctypes.byref(ptr),
            )
            if hr != 0 or not ptr:
                self.last_error = f"CoCreateInstance hr=0x{hr & 0xFFFFFFFF:08X}"
                return
        except Exception as exc:  # pragma: no cover - 仅 Windows 分支
            self.last_error = f"CoCreateInstance: {exc}"
            return

        self._ptr = ptr
        hr = self._call(3, ctypes.c_long)(ptr)
        if hr != 0:
            self.last_error = f"HrInit hr=0x{hr & 0xFFFFFFFF:08X}"
            self._release()
            self._ptr = None

    def _vtable(self):
        if self._ptr is None:
            return None
        return ctypes.cast(
            self._ptr,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents

    def _call(self, index: int, restype, *argtypes):
        vtable = self._vtable()
        if vtable is None:
            raise RuntimeError("taskbar COM object is not created")
        prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return prototype(vtable[index])

    def _set_state(self, state: int) -> None:
        if self._ptr is None or self._hwnd is None:
            return
        if self._last_state == state:
            return
        try:
            self._call(10, ctypes.c_long, wintypes.HWND, ctypes.c_int)(
                self._ptr, self._hwnd, state
            )
            self._last_state = state
        except Exception as exc:  # pragma: no cover - 仅 Windows 分支
            self.last_error = f"SetProgressState: {exc}"

    def _set_value(self, completed: int, total: int) -> None:
        if self._ptr is None or self._hwnd is None:
            return
        if self._last_value == (completed, total):
            return
        try:
            self._call(
                9,
                ctypes.c_long,
                wintypes.HWND,
                ctypes.c_ulonglong,
                ctypes.c_ulonglong,
            )(self._ptr, self._hwnd, completed, total)
            self._last_value = (completed, total)
        except Exception as exc:  # pragma: no cover - 仅 Windows 分支
            self.last_error = f"SetProgressValue: {exc}"

    def _release(self) -> None:
        if self._ptr is None:
            return
        try:
            self._call(2, ctypes.c_ulong)(self._ptr)
        except Exception:
            pass

    def _flush(self, force: bool = False) -> None:
        if not _IS_WINDOWS or self._ptr is None or self._hwnd is None:
            return
        if force:
            self._last_state = None
            self._last_value = None

        task: Optional[Tuple[str, Optional[float]]] = None
        for key in _TASK_PRIORITY:
            if key in self._tasks:
                task = self._tasks[key]
                break

        if task is None:
            self._set_state(_TBPF_NOPROGRESS)
            self._last_value = None
            return

        state, percent = task
        if state == "error":
            self._set_state(_TBPF_ERROR)
        elif state == "paused":
            self._set_state(_TBPF_PAUSED)
        elif percent is None:
            self._set_state(_TBPF_INDETERMINATE)
        else:
            self._set_state(_TBPF_NORMAL)

        if percent is not None:
            # 用 10000 作为分母，避免百分比取整造成卡顿
            self._set_value(int(percent * 100), 10000)
        else:
            self._last_value = None


taskbar_progress = TaskbarProgress()
#（注：内容由AI生成）
