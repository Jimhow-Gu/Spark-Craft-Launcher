'''动画刷新率工具：按窗口所在显示器动态限制到 30–120 FPS。'''

from __future__ import annotations

import math

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget

MAX_FPS = 120
MIN_FPS = 30
DEFAULT_HZ = 60.0


def _valid_hz(value) -> float:
    try:
        hz = float(value)
    except (TypeError, ValueError):
        return DEFAULT_HZ
    if not math.isfinite(hz) or hz <= 0:
        return DEFAULT_HZ
    return hz


def screen_refresh_rate(widget: QWidget | None) -> float:
    '''返回控件当前所在屏幕的刷新率；异常/0 时回退 60Hz。'''

    try:
        screen = widget.screen() if widget is not None else None
        if screen is None:
            screen = QApplication.primaryScreen()
        return _valid_hz(screen.refreshRate() if screen is not None else DEFAULT_HZ)
    except Exception:
        return DEFAULT_HZ


def target_refresh_rate(widget: QWidget | None, max_fps: int = MAX_FPS) -> float:
    hz = screen_refresh_rate(widget)
    try:
        limit = max(float(MIN_FPS), min(float(max_fps), float(MAX_FPS)))
    except (TypeError, ValueError):
        limit = float(MAX_FPS)
    return min(max(hz, float(MIN_FPS)), limit)


def frame_interval_ms(widget: QWidget | None, max_fps: int = MAX_FPS) -> int:
    '''计算 QTimer 间隔；120Hz 用 ceil 得 9ms（约 111 FPS，不超过 120）。'''

    hz = target_refresh_rate(widget, max_fps)
    return max(4, int(math.ceil(1000.0 / hz)))


class RefreshRateWatcher(QObject):
    '''监听当前屏幕刷新率；screenChanged 立即更新，并每秒轮询一次。'''

    interval_changed = Signal(int)

    def __init__(self, widget: QWidget, max_fps: int = MAX_FPS, parent=None):
        super().__init__(parent if parent is not None else widget)
        self._widget = widget
        self._max_fps = int(max_fps)
        self._interval = 0
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self.refresh)
        self._handle = None

    def _connect_screen_change(self):
        try:
            handle = self._widget.window().windowHandle()
        except (RuntimeError, AttributeError):
            handle = None
        if handle is None or handle is self._handle:
            return
        old = self._handle
        if old is not None:
            try:
                old.screenChanged.disconnect(self._on_screen_changed)
            except (RuntimeError, TypeError):
                pass
        self._handle = handle
        try:
            handle.screenChanged.connect(self._on_screen_changed)
        except (RuntimeError, TypeError):
            self._handle = None

    def _on_screen_changed(self, _screen=None):
        self.refresh()

    def refresh(self):
        # 窗口句柄可能因隐藏/重建而变化，轮询时顺便重新绑定。
        self._connect_screen_change()
        interval = frame_interval_ms(self._widget, self._max_fps)
        if interval != self._interval:
            self._interval = interval
            self.interval_changed.emit(interval)

    def start(self):
        self._connect_screen_change()
        self.refresh()
        if not self._poll.isActive():
            self._poll.start()

    def stop(self):
        self._poll.stop()
        old = self._handle
        self._handle = None
        if old is not None:
            try:
                old.screenChanged.disconnect(self._on_screen_changed)
            except (RuntimeError, TypeError):
                pass


__all__ = [
    'DEFAULT_HZ',
    'MAX_FPS',
    'MIN_FPS',
    'RefreshRateWatcher',
    'frame_interval_ms',
    'screen_refresh_rate',
    'target_refresh_rate',
]
