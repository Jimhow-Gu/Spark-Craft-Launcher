'''高对比度勾选框样式：未选中时也能清楚看到边框。'''

from __future__ import annotations
from PySide6.QtCore import (QPointF, QRectF, Qt)
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QProxyStyle, QStyle

class CheckBoxProxyStyle(QProxyStyle):
    '''在保留系统控件外观的前提下，重绘勾选框指示器。'''

    _light_mode = False

    @classmethod
    def set_light_mode(cls, light: bool):
        cls._light_mode = bool(light)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            return super().drawPrimitive(element, option, painter, widget)
        light = bool(getattr(type(self), '_light_mode', False))
        rect = QRectF(option.rect)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        side = max(12.0, min(16.0, min(rect.width(), rect.height()) - 1.0))
        box = QRectF(rect.center().x() - side / 2.0, rect.center().y() - side / 2.0, side, side)
        state = option.state
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        checked = bool(state & QStyle.StateFlag.State_On)
        partial = bool(state & QStyle.StateFlag.State_NoChange)
        hover = bool(state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            if checked or partial:
                fill = QColor('#0078d4') if enabled else QColor('#9aa0aa' if light else '#3b414a')
                if hover and enabled:
                    border = QColor('#4da3ff')
                else:
                    border = QColor('#0078d4') if enabled else QColor('#8a909a' if light else '#4c525c')
            elif light:
                fill = QColor(255, 255, 255, 235 if enabled else 180)
                border = QColor('#6b7280') if enabled else QColor('#c0c4cc')
                if hover and enabled:
                    border = QColor('#0078d4')
                    fill = QColor(0, 120, 212, 28)
            else:
                fill = QColor(255, 255, 255, 22 if enabled else 10)
                border = QColor('#b6bfd0') if enabled else QColor('#5b616b')
                if hover and enabled:
                    border = QColor('#4da3ff')
                    fill = QColor(0, 120, 212, 38)
            painter.setBrush(fill)
            painter.setPen(QPen(border, 1.3))
            painter.drawRoundedRect(box.adjusted(0.65, 0.65, -0.65, -0.65), 3.0, 3.0)
            if checked:
                pen = QPen(QColor('#ffffff') if enabled else QColor('#8d939d'), max(1.7, side * 0.13))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                path = QPainterPath()
                path.moveTo(box.left() + side * 0.24, box.top() + side * 0.53)
                path.lineTo(box.left() + side * 0.43, box.top() + side * 0.71)
                path.lineTo(box.left() + side * 0.76, box.top() + side * 0.31)
                painter.drawPath(path)
            elif partial:
                pen = QPen(QColor('#ffffff') if enabled else QColor('#8d939d'), max(1.7, side * 0.13))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(QPointF(box.left() + side * 0.22, box.center().y()), QPointF(box.right() - side * 0.22, box.center().y()))
                return
        finally:
            painter.restore()


