'''左侧栏：自绘左下圆角，避免 QSS 圆角锯齿。'''

from PySide6.QtCore import Qt
from PySide6.QtGui import (QColor, QPainter, QPainterPath)
from PySide6.QtWidgets import QWidget

class SidePanel(QWidget):
    def __init__(self, parent=None, radius: int=14, color=None):
        super().__init__(parent)
        self._radius = int(radius)
        self._color = QColor(color or QColor(16, 18, 24, 150))
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def set_light(self, light: bool):
        self._color = (
            QColor(246, 247, 249, 210)
            if light else QColor(16, 18, 24, 150)
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        r = self._radius
        path = QPainterPath()
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left() + r, rect.bottom())
        path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - r)
        path.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawPath(path)
        painter.fillRect(rect.left(), rect.top(), rect.width(), 1, self._color)
        painter.end()


