'''可旋转的折叠箭头：统一使用细线 chevron，并带展开/收起动画。'''

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, Property, QPointF, QPropertyAnimation, Qt,
)
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication


class RotatingChevronButton(QAbstractButton):
    '''自绘 chevron，不依赖字体三角，展开/收起时旋转而不是换字符。'''

    def __init__(
        self,
        parent=None,
        *,
        size=28,
        collapsed_angle=-90.0,
        expanded_angle=0.0,
        light_color='#2F6F6F',
        dark_color='#A8E6E6',
    ):
        super().__init__(parent)
        self._collapsed_angle = float(collapsed_angle)
        self._expanded_angle = float(expanded_angle)
        self._angle = self._collapsed_angle
        self._light_color = QColor(light_color)
        self._dark_color = QColor(dark_color)
        try:
            self._light = (
                QApplication.palette().color(QPalette.Window).lightness() > 128
            )
        except Exception:
            self._light = False
        self.setFixedSize(int(size), int(size))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setProperty('_theme_ignore', True)
        self._anim = QPropertyAnimation(self, b'angle', self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def get_angle(self):
        return self._angle

    def set_angle(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if abs(value - self._angle) < 0.01:
            return
        self._angle = value
        self.update()

    angle = Property(float, get_angle, set_angle)

    def set_expanded(self, expanded, animate=True):
        target = self._expanded_angle if expanded else self._collapsed_angle
        self._anim.stop()
        if not animate or not self.isVisible():
            self.set_angle(target)
            return
        self._anim.setStartValue(float(self._angle))
        self._anim.setEndValue(float(target))
        self._anim.start()

    def set_light(self, light):
        light = bool(light)
        if light == self._light:
            return
        self._light = light
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        side = max(8.0, float(min(self.width(), self.height())))
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.rotate(self._angle)
        width = side * 0.42
        height = side * 0.22
        color = self._light_color if self._light else self._dark_color
        pen = QPen(
            color,
            max(1.6, side * 0.075),
            Qt.SolidLine,
            Qt.RoundCap,
            Qt.RoundJoin,
        )
        painter.setPen(pen)
        painter.drawLine(
            QPointF(-width / 2.0, -height / 2.0),
            QPointF(0.0, height / 2.0),
        )
        painter.drawLine(
            QPointF(0.0, height / 2.0),
            QPointF(width / 2.0, -height / 2.0),
        )
        painter.end()


__all__ = ['RotatingChevronButton']
