'''高对比度勾选框样式：未选中时也能清楚看到边框。'''

from __future__ import annotations
from PySide6.QtCore import (QPointF, QRectF, Qt)
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPalette, QPen)
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleOptionSlider

from ui.design import (
    ACCENT, ACCENT_HOVER, ON_ACCENT, PRIMARY, PRIMARY_HOVER,
)

class CheckBoxProxyStyle(QProxyStyle):
    '''在保留系统控件外观的前提下，重绘勾选框指示器。'''

    _light_mode = False

    @classmethod
    def set_light_mode(cls, light: bool):
        cls._light_mode = bool(light)

    def drawComplexControl(self, control, option, painter, widget=None):
        if control == QStyle.ComplexControl.CC_Slider and isinstance(option, QStyleOptionSlider) and option.orientation == Qt.Horizontal:
            return self._draw_slider(option, painter)
        return super().drawComplexControl(control, option, painter, widget)

    def _draw_slider(self, option, painter):
        '''自绘水平滑条：圆角轨道 + 正圆滑钮，避免 QSS 画成方块/圆角矩形。'''

        rect = QRectF(option.rect)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        light = option.palette.color(QPalette.Window).lightness() > 128
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        span = max(1, int(option.maximum) - int(option.minimum))
        value = int(option.sliderPosition)
        if value < 0:
            value = int(option.sliderValue)
        value = max(int(option.minimum), min(int(option.maximum), value))
        ratio = max(0.0, min(1.0, (value - int(option.minimum)) / span))
        handle_radius = 8.0
        groove_height = 6.0
        left = rect.left() + handle_radius
        right = rect.right() - handle_radius
        center_y = rect.center().y()
        groove = QRectF(left, center_y - groove_height / 2.0, max(0.0, right - left), groove_height)
        if groove.width() <= 0:
            return
        track = (
            QColor(144, 217, 217, 80) if light
            else QColor(144, 217, 217, 42)
        )
        if not enabled:
            track = QColor(128, 128, 128, 32)
        handle_fill = QColor(PRIMARY) if enabled else QColor('#9aa0aa' if light else '#4c525c')
        if enabled and hover:
            handle_fill = QColor(PRIMARY_HOVER)
        handle_border = QColor('#F8FBFB') if light else QColor('#121717')
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            radius = groove_height / 2.0
            painter.setPen(Qt.NoPen)
            painter.setBrush(track)
            painter.drawRoundedRect(groove, radius, radius)
            handle_x = left + groove.width() * ratio
            if ratio > 0.001:
                fill = QRectF(groove.left(), groove.top(), max(radius * 2.0, handle_x - groove.left()), groove.height())
                painter.setBrush(QColor(PRIMARY) if enabled else QColor('#7d898d'))
                painter.drawRoundedRect(fill, radius, radius)
            painter.setBrush(handle_fill)
            painter.setPen(QPen(handle_border, 2.0))
            painter.drawEllipse(
                QPointF(handle_x, center_y), handle_radius, handle_radius
            )
        finally:
            painter.restore()

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_IndicatorRadioButton:
            return self._draw_radio(option, painter)
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
                fill = QColor(PRIMARY) if enabled else QColor('#9aa0aa' if light else '#3b414a')
                if hover and enabled:
                    border = QColor(PRIMARY_HOVER)
                else:
                    border = QColor(PRIMARY) if enabled else QColor('#8a909a' if light else '#4c525c')
            elif light:
                fill = QColor(255, 255, 255, 235 if enabled else 180)
                border = QColor('#6b7280') if enabled else QColor('#c0c4cc')
                if hover and enabled:
                    border = QColor(PRIMARY)
                    fill = QColor(144, 217, 217, 28)
            else:
                fill = QColor(255, 255, 255, 22 if enabled else 10)
                border = QColor('#b6bfd0') if enabled else QColor('#5b616b')
                if hover and enabled:
                    border = QColor(PRIMARY_HOVER)
                    fill = QColor(144, 217, 217, 38)
            painter.setBrush(fill)
            painter.setPen(QPen(border, 1.3))
            painter.drawRoundedRect(box.adjusted(0.65, 0.65, -0.65, -0.65), 3.0, 3.0)
            if checked:
                pen = QPen(QColor(ON_ACCENT) if enabled else QColor('#8d939d'), max(1.7, side * 0.13))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                path = QPainterPath()
                path.moveTo(box.left() + side * 0.24, box.top() + side * 0.53)
                path.lineTo(box.left() + side * 0.43, box.top() + side * 0.71)
                path.lineTo(box.left() + side * 0.76, box.top() + side * 0.31)
                painter.drawPath(path)
            elif partial:
                pen = QPen(QColor(ON_ACCENT) if enabled else QColor('#8d939d'), max(1.7, side * 0.13))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(QPointF(box.left() + side * 0.22, box.center().y()), QPointF(box.right() - side * 0.22, box.center().y()))
                return
        finally:
            painter.restore()

    def _draw_radio(self, option, painter):
        '''单选按钮始终绘制为正圆，避免 QSS 把 indicator 画成圆角方块。'''

        light = bool(getattr(type(self), '_light_mode', False))
        rect = QRectF(option.rect)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        side = max(12.0, min(16.0, min(rect.width(), rect.height()) - 1.0))
        box = QRectF(
            rect.center().x() - side / 2.0,
            rect.center().y() - side / 2.0,
            side, side,
        )
        state = option.state
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        checked = bool(state & QStyle.StateFlag.State_On)
        hover = bool(state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            if checked:
                fill = QColor(ACCENT) if enabled else QColor('#9aa0aa' if light else '#3b414a')
                border = QColor(ACCENT_HOVER if hover and enabled else ACCENT) if enabled else QColor('#8a909a' if light else '#4c525c')
            elif light:
                fill = QColor(255, 255, 255, 235 if enabled else 180)
                border = QColor(ACCENT) if hover and enabled else QColor('#6b7280' if enabled else '#c0c4cc')
            else:
                fill = QColor(255, 255, 255, 22 if enabled else 10)
                border = QColor(ACCENT_HOVER) if hover and enabled else QColor('#b6bfd0' if enabled else '#5b616b')
            painter.setBrush(fill)
            painter.setPen(QPen(border, 1.3))
            dot_box = box.adjusted(0.65, 0.65, -0.65, -0.65)
            painter.drawEllipse(dot_box)
            if checked:
                inner_side = side * 0.38
                inner = QRectF(
                    box.center().x() - inner_side / 2.0,
                    box.center().y() - inner_side / 2.0,
                    inner_side, inner_side,
                )
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(ON_ACCENT) if enabled else QColor('#8d939d' if light else '#6b7280'))
                painter.drawEllipse(inner)
        finally:
            painter.restore()


