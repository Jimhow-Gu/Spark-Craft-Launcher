'''统一主色滑块：圆角轨道、圆角已填充轨道和圆形滑钮。'''

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QSlider

from ui.design import PRIMARY, PRIMARY_HOVER


class PrimarySlider(QSlider):
    '''不依赖平台样式/QSS，避免不同系统下轨道被画成方块。'''

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(20)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self.orientation() != Qt.Horizontal:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            rect = QRectF(self.rect())
            if rect.width() <= 0 or rect.height() <= 0:
                return
            light = self.palette().color(QPalette.Window).lightness() > 128
            enabled = self.isEnabled()
            hover = self.underMouse() and enabled
            handle_radius = max(5.0, min(8.0, rect.height() / 2.0 - 1.0))
            groove_height = max(4.0, min(6.0, rect.height() * 0.34))
            left = rect.left() + handle_radius
            right = rect.right() - handle_radius
            center_y = rect.center().y()
            groove = QRectF(
                left,
                center_y - groove_height / 2.0,
                max(0.0, right - left),
                groove_height,
            )
            if groove.width() <= 0:
                return
            span = max(1, int(self.maximum()) - int(self.minimum()))
            value = max(int(self.minimum()), min(int(self.maximum()), int(self.value())))
            ratio = max(0.0, min(1.0, (value - int(self.minimum())) / span))

            track = QColor(144, 217, 217, 80 if light else 42)
            if not enabled:
                track = QColor(128, 128, 128, 32)
            handle_fill = QColor(PRIMARY) if enabled else QColor('#9aa0aa' if light else '#4c525c')
            if hover:
                handle_fill = QColor(PRIMARY_HOVER)
            handle_border = QColor('#F8FBFB') if light else QColor('#121717')

            radius = groove_height / 2.0
            painter.setPen(Qt.NoPen)
            painter.setBrush(track)
            painter.drawRoundedRect(groove, radius, radius)

            handle_x = left + groove.width() * ratio
            if ratio > 0.001:
                fill = QRectF(
                    groove.left(),
                    groove.top(),
                    max(radius * 2.0, handle_x - groove.left()),
                    groove.height(),
                )
                painter.setBrush(QColor(PRIMARY) if enabled else QColor('#7d898d'))
                painter.drawRoundedRect(fill, radius, radius)

            painter.setBrush(handle_fill)
            painter.setPen(QPen(handle_border, 2.0))
            painter.drawEllipse(QPointF(handle_x, center_y), handle_radius, handle_radius)
        finally:
            painter.end()


__all__ = ['PrimarySlider']
