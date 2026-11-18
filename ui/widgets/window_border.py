'''窗口最外层描边：一圈细边框（顶部圆角与标题栏一致）。'''

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import QWidget


class WindowBorderOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
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
            color = QColor(0, 0, 0, 48) if light else QColor(255, 255, 255, 32)

            # 外圈 14、内圈 13：用两个圆角路径相减后填充 1px 环。
            # 不要用 QPen 画线，笔宽会跨在路径两侧，被窗口 mask 裁掉半像素。
            outer_rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
            inner_rect = outer_rect.adjusted(1.0, 1.0, -1.0, -1.0)
            outer = QPainterPath()
            outer.addRoundedRect(outer_rect, 14.0, 14.0)
            inner = QPainterPath()
            inner.addRoundedRect(inner_rect, 13.0, 13.0)
            ring = outer.subtracted(inner)
            painter.setPen(Qt.NoPen)
            painter.setBrush(Qt.NoBrush)
            painter.fillPath(ring, color)
            painter.end()
        except Exception:
            pass
