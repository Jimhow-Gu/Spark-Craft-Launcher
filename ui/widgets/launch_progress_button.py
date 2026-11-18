'''启动按钮：准备/启动阶段整体作为带填充动画的进度条。'''

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QEvent, QPropertyAnimation, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton

from core.app_font import ui_font
from ui.design import ON_ACCENT, PRIMARY
from ui.refresh_rate import RefreshRateWatcher, frame_interval_ms


class LaunchProgressButton(QPushButton):
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self._progress = 0.0
        self._working = False
        self._starting = False
        self._show_percent = True
        self._phase = 0.0
        self._anim = QPropertyAnimation(self, b'progress', self)
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._shimmer = QTimer(self)
        self._shimmer.setTimerType(Qt.PreciseTimer)
        self._shimmer.setInterval(frame_interval_ms(self, 60))
        self._shimmer.timeout.connect(self._tick)
        self._rate_watcher = RefreshRateWatcher(self, 60, self)
        self._rate_watcher.interval_changed.connect(self._on_frame_interval)
        self._window = None
        self.setFont(ui_font(14, QFont.DemiBold))

    def get_progress(self):
        return self._progress

    def set_progress(self, value):
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = Property(float, get_progress, set_progress)

    def set_idle(self, text='启动游戏'):
        self._anim.stop()
        self._working = False
        self._starting = False
        self._progress = 0.0
        self._stop_shimmer()
        self.setText(text)
        self.setEnabled(True)
        self.update()

    def set_working(self, text='取消', progress=0.0, animate=True, show_percent=True, starting=False):
        self._working = True
        self._starting = bool(starting)
        self._show_percent = bool(show_percent)
        self.setText(text)
        self.setEnabled(True)
        target = max(0.0, min(1.0, float(progress or 0.0)))
        self._anim.stop()
        if animate and self.isVisible():
            self._anim.setStartValue(self._progress)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._progress = target
        if not self._shimmer.isActive():
            self._start_shimmer()
        self.update()

    def set_disabled_state(self, text):
        self._anim.stop()
        self._working = False
        self._starting = False
        self._stop_shimmer()
        self._progress = 0.0
        self.setText(text)
        self.setEnabled(False)
        self.update()

    def _on_frame_interval(self, interval):
        interval = max(4, int(interval))
        if self._shimmer.interval() != interval:
            self._shimmer.setInterval(interval)

    def _start_shimmer(self):
        self._shimmer.setInterval(frame_interval_ms(self, 60))
        self._rate_watcher.start()
        self._shimmer.start()

    def _stop_shimmer(self):
        self._shimmer.stop()
        self._rate_watcher.stop()

    def _watch_window(self):
        try:
            window = self.window()
        except (RuntimeError, AttributeError):
            return
        if window is self._window:
            return
        old = self._window
        if old is not None:
            try:
                old.removeEventFilter(self)
            except (RuntimeError, AttributeError):
                pass
        self._window = window
        if window is not None:
            window.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._window and event.type() == QEvent.WindowStateChange:
            try:
                minimized = obj.isMinimized()
            except (RuntimeError, AttributeError):
                minimized = False
            if minimized:
                self._stop_shimmer()
            elif self._working:
                self._start_shimmer()
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._watch_window()
        if self._working:
            self._start_shimmer()

    def hideEvent(self, event):
        self._stop_shimmer()
        super().hideEvent(event)

    def _tick(self):
        if not self._working:
            self._stop_shimmer()
            return
        try:
            if not self.isVisible() or self.window().isMinimized():
                self._stop_shimmer()
                return
        except (RuntimeError, AttributeError):
            pass
        # 保持原 30fps 时的流动速度，不因刷新率提高而加速。
        factor = self._shimmer.interval() / 33.0
        self._phase = (self._phase + 0.022 * factor) % 1.28
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 10.0
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)
        enabled = self.isEnabled()
        if not enabled:
            base = QColor(128, 128, 128, 64)
        elif self._working:
            base = QColor(24, 33, 35)
        else:
            base = QColor(PRIMARY)
        painter.fillPath(path, base)
        if self._working:
            fill = QRectF(rect.left(), rect.top(), max(2.0, rect.width() * self._progress), rect.height())
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill, radius, radius)
            painter.fillPath(fill_path, QColor(PRIMARY))
            # 流动高光：证明按钮在持续推进，而不是卡住
            gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
            center = self._phase % 1.2
            gradient.setColorAt(max(0.0, center - 0.18), QColor(255, 255, 255, 0))
            gradient.setColorAt(min(1.0, center), QColor(255, 255, 255, 58 if not self._starting else 38))
            gradient.setColorAt(min(1.0, center + 0.18), QColor(255, 255, 255, 0))
            painter.fillRect(fill, gradient)
        painter.setPen(QPen(Qt.NoPen))
        text = self.text()
        if self._working and self._show_percent:
            text = f'{text}   {int(round(self._progress * 100))}%'
        if not enabled:
            color = QColor(138, 143, 152)
        elif self._working and self._progress < 0.55:
            color = QColor(255, 255, 255)
        else:
            color = QColor(ON_ACCENT)
        painter.setPen(color)
        painter.setFont(self.font())
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.end()


__all__ = ['LaunchProgressButton']
