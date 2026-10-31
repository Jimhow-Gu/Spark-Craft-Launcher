'''左侧栏：自绘左下圆角，避免 QSS 圆角锯齿。'''

from PySide6.QtCore import Qt
from PySide6.QtGui import (QColor, QPainter, QPainterPath)
from PySide6.QtWidgets import QWidget

class SidePanel(QWidget):
    def __init__(self, parent=None, radius: int=14, color=None):
        super().__init__(parent)
        self._radius = int(radius)
        self._light = False
        self._expanded = False
        self._color = QColor(color or QColor(16, 18, 24, 150))
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def set_light(self, light: bool):
        self._light = bool(light)
        self._refresh_color()

    def set_expanded(self, expanded: bool):
        self._expanded = bool(expanded)
        self._refresh_color()

    def _refresh_color(self):
        # 展开前后保持同一透明度，避免覆盖内容时突然变成不透明。
        if self._light:
            self._color = QColor(246, 247, 249, 210)
        else:
            self._color = QColor(16, 18, 24, 150)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        right = rect.right() + 1
        bottom = rect.bottom() + 1
        r = self._radius
        path = QPainterPath()
        path.moveTo(rect.left() + r, rect.top())
        path.lineTo(right, rect.top())
        path.lineTo(right, bottom)
        path.lineTo(rect.left() + r, bottom)
        path.quadTo(rect.left(), bottom, rect.left(), bottom - r)
        path.lineTo(rect.left(), rect.top() + r)
        path.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
        path.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawPath(path)
        edge = (
            QColor(0, 0, 0, 18) if self._light
            else QColor(255, 255, 255, 18)
        )
        painter.fillRect(rect.right(), rect.top(), 1, rect.height(), edge)
        painter.end()


