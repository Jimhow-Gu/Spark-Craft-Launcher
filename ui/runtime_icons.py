'''运行中的主题图标：浅色/深色两套，以及 Windows 任务栏 HICON 切换。

exe 文件图标和固定到任务栏的快捷方式使用 sparkler_neutral.ico，由
PyInstaller 在编译期写入 PE；这里只处理进程运行期间会变化的图标。
'''

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

_IMAGE_ROOT = Path(__file__).resolve().parent / 'imgs'


def _resource_path(relative: str) -> Path:
    # 与 main.resource_path 保持一致：onefile 下从 _MEIPASS 读取。
    base = Path(getattr(sys, '_MEIPASS', _IMAGE_ROOT.parent.parent))
    return base / relative


def runtime_icon_paths(light: bool):
    suffix = 'light' if light else 'dark'
    return (
        _resource_path(f'ui/imgs/sparkler_{suffix}.ico'),
        _resource_path(f'ui/imgs/sparkler_{suffix}.png'),
    )


def runtime_icon(light: bool) -> QIcon:
    ico_path, png_path = runtime_icon_paths(light)
    icon = QIcon()
    if ico_path.is_file():
        icon.addFile(str(ico_path))
    if png_path.is_file():
        icon.addFile(str(png_path))
    return icon


def apply_runtime_icon(light: bool, window=None) -> QIcon:
    '''切换 QApplication/主窗口图标，不触碰 exe 和固定快捷方式。'''

    icon = runtime_icon(light)
    app = QApplication.instance()
    if app is not None and not icon.isNull():
        app.setWindowIcon(icon)
    if window is not None and not icon.isNull():
        try:
            window.setWindowIcon(icon)
        except RuntimeError:
            return icon
        try:
            handle = window.windowHandle()
            if handle is not None:
                handle.setIcon(icon)
        except (RuntimeError, AttributeError):
            pass
        # 运行中切换主题时同步任务栏/Alt-Tab 的 HICON；窗口还没显示时
        # 由 main.py 在 show() 后统一写入，避免提前创建原生窗口。
        try:
            if window.isVisible():
                apply_windows_window_icon(window, runtime_icon_paths(light)[0])
        except (RuntimeError, AttributeError):
            pass
    return icon


def _destroy_icon(handle):
    if not handle:
        return
    try:
        user32 = ctypes.windll.user32
        user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        user32.DestroyIcon.restype = ctypes.c_bool
        user32.DestroyIcon(ctypes.c_void_p(handle))
    except Exception:
        pass


def apply_windows_window_icon(window, icon_path) -> None:
    '''把 ICO 写入任务栏/标题栏/Alt-Tab，并释放上一次的 HICON。'''

    if sys.platform != 'win32' or window is None:
        return
    path = str(icon_path or '')
    if not path or not Path(path).is_file():
        return
    # 同一路径重复调用时直接跳过，避免重复创建和销毁句柄。
    if getattr(window, '_taskbar_icon_path', '') == path:
        return
    try:
        hwnd = int(window.winId())
    except (RuntimeError, ValueError):
        return
    if not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        ICON_SMALL, ICON_BIG, ICON_SMALL2 = 0, 1, 2
        GCLP_HICON, GCLP_HICONSM = -14, -34
        # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE |
        # SWP_NOZORDER | SWP_NOACTIVATE
        SWP_FLAGS = 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010

        try:
            user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
            user32.GetDpiForWindow.restype = ctypes.c_uint
            dpi = int(user32.GetDpiForWindow(ctypes.c_void_p(hwnd))) or 96
        except Exception:
            dpi = 96
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        try:
            # Win10 1607+：按窗口所在显示器 DPI 取任务栏/标题栏尺寸，
            # 覆盖 125%/150%/175% 缩放。
            user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, ctypes.c_uint]
            user32.GetSystemMetricsForDpi.restype = ctypes.c_int
            small_cx = int(user32.GetSystemMetricsForDpi(49, dpi))
            small_cy = int(user32.GetSystemMetricsForDpi(50, dpi))
            big_cx = int(user32.GetSystemMetricsForDpi(11, dpi))
            big_cy = int(user32.GetSystemMetricsForDpi(12, dpi))
        except Exception:
            scale = max(1.0, dpi / 96.0)
            small_cx = int(round(user32.GetSystemMetrics(49) * scale))
            small_cy = int(round(user32.GetSystemMetrics(50) * scale))
            big_cx = int(round(user32.GetSystemMetrics(11) * scale))
            big_cy = int(round(user32.GetSystemMetrics(12) * scale))

        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        hicon_small = user32.LoadImageW(
            None, path, IMAGE_ICON, small_cx, small_cy, LR_LOADFROMFILE
        )
        hicon_big = user32.LoadImageW(
            None, path, IMAGE_ICON, big_cx, big_cy, LR_LOADFROMFILE
        )
        if not hicon_small:
            hicon_small = hicon_big
        if not hicon_big:
            hicon_big = hicon_small
        if not hicon_small and not hicon_big:
            return

        old = tuple(getattr(window, '_taskbar_icon_handles', ()) or ())
        window._taskbar_icon_handles = (
            int(hicon_small or 0), int(hicon_big or 0)
        )
        window._taskbar_icon_path = path
        # 兼容旧代码可能读取的单句柄字段。
        window._taskbar_icon_handle = int(hicon_big or hicon_small or 0)

        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        for icon_id, handle in (
            (ICON_SMALL, hicon_small),
            (ICON_BIG, hicon_big),
            (ICON_SMALL2, hicon_small),
        ):
            if handle:
                user32.SendMessageW(
                    ctypes.c_void_p(hwnd), WM_SETICON,
                    ctypes.c_void_p(icon_id), ctypes.c_void_p(handle),
                )

        try:
            user32.SetClassLongPtrW.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
            ]
            user32.SetClassLongPtrW.restype = ctypes.c_void_p
            if hicon_big:
                user32.SetClassLongPtrW(
                    ctypes.c_void_p(hwnd), GCLP_HICON,
                    ctypes.c_void_p(hicon_big),
                )
            if hicon_small:
                user32.SetClassLongPtrW(
                    ctypes.c_void_p(hwnd), GCLP_HICONSM,
                    ctypes.c_void_p(hicon_small),
                )
        except Exception:
            pass

        try:
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = ctypes.c_bool
            user32.SetWindowPos(
                ctypes.c_void_p(hwnd), ctypes.c_void_p(0),
                0, 0, 0, 0, SWP_FLAGS,
            )
        except Exception:
            pass

        # 新句柄已经交给窗口/窗口类后再销毁旧句柄。
        for handle in old:
            if handle and handle not in (hicon_small, hicon_big):
                _destroy_icon(handle)
    except Exception:
        return


def release_windows_window_icon(window) -> None:
    '''窗口销毁前释放 HICON；重复调用安全。'''

    if window is None:
        return
    for handle in tuple(getattr(window, '_taskbar_icon_handles', ()) or ()):
        _destroy_icon(handle)
    window._taskbar_icon_handles = ()
    window._taskbar_icon_path = ''
    window._taskbar_icon_handle = 0


__all__ = [
    'apply_runtime_icon',
    'apply_windows_window_icon',
    'release_windows_window_icon',
    'runtime_icon',
    'runtime_icon_paths',
]
