'''抗锯齿圆角卡片：替代 QSS 边框，避免四个角出现阶梯状毛边。'''

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QFrame, QGroupBox


def _is_light(_widget):
    try:
        palette = QApplication.palette()
        return palette.color(QPalette.Window).lightness() > 128
    except Exception:
        return False


class _RoundedSurface:
    '''为 QFrame/QGroupBox 提供统一的自绘圆角背景和 1px 描边。'''

    def _init_surface(self, radius=12):
        self._surface_radius = float(radius)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

    def set_light(self, _light):
        self.update()

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self.update()
        super().changeEvent(event)

    def paintEvent(self, _event):
        light = _is_light(self)
        background = (
            QColor(248, 251, 251, 224)
            if light else QColor(30, 41, 41, 224)
        )
        border = (
            QColor(0, 0, 0, 28)
            if light else QColor(255, 255, 255, 56)
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._surface_radius, self._surface_radius)
        painter.fillPath(path, background)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)
        title = self.title() if isinstance(self, QGroupBox) else ''
        if title:
            painter.setPen(QColor(34, 34, 34) if light else QColor(240, 240, 240))
            font_metrics = self.fontMetrics()
            painter.drawText(
                QRectF(20, 2, self.width() - 40, font_metrics.height() + 4),
                Qt.AlignLeft | Qt.AlignVCenter,
                title,
            )
        painter.end()


class RoundedFrame(_RoundedSurface, QFrame):
    def __init__(self, parent=None, *, radius=12):
        super().__init__(parent)
        self._init_surface(radius)


class RoundedGroupBox(_RoundedSurface, QGroupBox):
    def __init__(self, parent=None, *, radius=12):
        super().__init__(parent)
        self._init_surface(radius)


__all__ = ['RoundedFrame', 'RoundedGroupBox']
