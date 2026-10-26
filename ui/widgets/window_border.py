'''窗口最外层描边：一圈细边框（顶部圆角与标题栏一致）。'''

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QWidget


class WindowBorderOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._light = False

    def set_light(self, light: bool):
        self._light = bool(light)
        self.update()

    def _is_light(self):
        from PySide6.QtWidgets import QApplication
        try:
            return QApplication.palette().color(QPalette.Window).lightness() > 128
        except Exception:
            return self._light

    def changeEvent(self, event):
        if event.type() == QEvent.PaletteChange:
            self.update()
        super().changeEvent(event)

    def paintEvent(self, event):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            light = self._is_light()
            color = QColor(0, 0, 0, 55) if light else QColor(255, 255, 255, 40)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            radius = 14.0
            path = QPainterPath()
            path.moveTo(rect.left() + radius, rect.top())
            path.lineTo(rect.right() - radius, rect.top())
            path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
            path.lineTo(rect.right(), rect.bottom() - radius)
            path.quadTo(rect.right(), rect.bottom(), rect.right() - radius, rect.bottom())
            path.lineTo(rect.left() + radius, rect.bottom())
            path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - radius)
            path.lineTo(rect.left(), rect.top() + radius)
            path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
            path.closeSubpath()
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            painter.end()
        except Exception:
            pass
