"""主题切换时的圆形扩散动画。"""
from __future__ import annotations

import math

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget


class ThemeRevealOverlay(QWidget):
    """把旧主题截图盖在新界面上，用不断扩大的圆洞露出新主题。"""

    done = Signal()

    def __init__(self, parent, pixmap: QPixmap, center, duration: int = 420):
        super().__init__(parent)
        self._pixmap = pixmap
        self._center = center
        self._radius = 0.0
        self._duration = max(120, int(duration))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setGeometry(parent.rect())
        self.raise_()
        self._anim = QPropertyAnimation(self, b"radius", self)
        self._anim.setDuration(self._duration)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(float(self._max_radius()))
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._finish)

    def _max_radius(self) -> float:
        rect = self.rect()
        cx, cy = self._center.x(), self._center.y()
        points = (
            (rect.left(), rect.top()),
            (rect.right(), rect.top()),
            (rect.left(), rect.bottom()),
            (rect.right(), rect.bottom()),
        )
        return max(
            math.hypot(px - cx, py - cy) for px, py in points
        ) + 8.0

    def get_radius(self) -> float:
        return self._radius

    def set_radius(self, value: float):
        self._radius = float(value)
        self.update()

    radius = Property(float, get_radius, set_radius)

    def start(self):
        self.show()
        self.raise_()
        self._anim.start()

    def _finish(self):
        try:
            self.hide()
        except RuntimeError:
            return
        try:
            self.done.emit()
        except RuntimeError:
            pass
        try:
            self.deleteLater()
        except RuntimeError:
            pass

    def paintEvent(self, event):
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        path.addRect(rect)
        path.addEllipse(self._center, self._radius, self._radius)
        painter.setClipPath(path)
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.end()
