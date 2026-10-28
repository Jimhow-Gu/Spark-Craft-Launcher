'''带平滑填充动画的进度条。'''

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QProgressBar, QStyle, QStyleOptionProgressBar


class RoundedProgressBar(QProgressBar):
    '''始终按圆角裁剪轨道和填充，低进度时也保持圆角。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fill_color = QColor('#0078d4')

    def set_fill_color(self, color):
        value = QColor(color)
        if value.isValid() and value != self._fill_color:
            self._fill_color = value
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = min(6.0, max(2.0, rect.height() / 2.0))
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)
        groove = QStyleOptionProgressBar()
        self.initStyleOption(groove)
        groove.progress = groove.minimum
        groove.text = ''
        groove.textVisible = False
        self.style().drawControl(QStyle.CE_ProgressBar, groove, painter, self)
        if self.value() > self.minimum() and option.maximum > option.minimum and rect.width() > 0:
            ratio = max(0.0, min(1.0, (self.value() - option.minimum) / (option.maximum - option.minimum)))
            fill_width = rect.width() * ratio
            # 低进度时也至少画出一个圆角小段，避免左侧呈现直角细条。
            fill_width = max(fill_width, min(rect.width(), max(12.0, radius * 2)))
            fill = QRectF(rect.left(), rect.top(), min(rect.width(), fill_width), rect.height())
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill, radius, radius)
            painter.fillPath(fill_path, self._fill_color)
        painter.end()


class SmoothProgressBar(RoundedProgressBar):
    '''数值变化时从左往右平滑推进，而不是瞬间跳变。'''

    def __init__(self, parent=None, duration: int=260):
        super().__init__(parent)
        self._duration = max(80, int(duration))
        self._anim = QPropertyAnimation(self, b'value', self)
        self._anim.setDuration(self._duration)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def set_animated_value(self, value, animate: bool=True):
        try:
            target = int(round(float(value)))
        except (TypeError, ValueError):
            return
        if self.maximum() <= self.minimum():
            self._anim.stop()
            super().setValue(self.minimum())
            return
        target = max(self.minimum(), min(self.maximum(), target))
        if not animate or not self.isVisible():
            self._anim.stop()
            super().setValue(target)
            return
        if target == self.value():
            return
        self._anim.stop()
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(target)
        self._anim.start()

    def reset_value(self):
        '''立即回到起点，用于新任务开始或隐藏后复用。'''

        self._anim.stop()
        super().setValue(self.minimum())


__all__ = ['RoundedProgressBar', 'SmoothProgressBar']


