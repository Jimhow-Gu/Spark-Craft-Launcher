'''带平滑填充动画的进度条。'''

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QProgressBar, QStyle, QStyleOptionProgressBar


class RoundedProgressBar(QProgressBar):
    '''始终按圆角裁剪轨道和填充，避免首次 0% 绘制成直角矩形。'''

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
        self.style().drawControl(QStyle.CE_ProgressBar, option, painter, self)
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


