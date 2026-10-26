'''全局平滑滚轮：列表、表格和滚动页面使用动画滚动。'''

from __future__ import annotations
from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt)
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QComboBox, QScrollBar, QSlider, QWidget)

class SmoothScroller(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._anims = {}
        self._targets = {}

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel:
            return False
        if isinstance(obj, (QComboBox, QSlider, QScrollBar)):
            return False
        widget = obj if isinstance(obj, QWidget) else None
        area = None
        while widget is not None:
            if isinstance(widget, (QComboBox, QSlider)):
                return False
            if isinstance(widget, QAbstractScrollArea):
                area = widget
                break
            widget = widget.parentWidget()
        if area is None:
            return False
        try:
            state = event
            pixel = state.pixelDelta()
            angle = state.angleDelta()
            use_horizontal = abs(angle.x()) > abs(angle.y()) or abs(pixel.x()) > abs(pixel.y()) or bool(state.modifiers() & Qt.ShiftModifier)
            if use_horizontal:
                bar = area.horizontalScrollBar()
            else:
                bar = area.verticalScrollBar()
            if bar is None or bar.maximum() <= bar.minimum():
                bar = area.horizontalScrollBar() if not use_horizontal else area.verticalScrollBar()
            if bar is None or bar.maximum() <= bar.minimum():
                return False
            if isinstance(area, QAbstractItemView):
                if use_horizontal:
                    if area.horizontalScrollMode() != QAbstractItemView.ScrollPerPixel:
                        area.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
                elif area.verticalScrollMode() != QAbstractItemView.ScrollPerPixel:
                    area.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
            if use_horizontal:
                delta = pixel.x() if pixel.x() else angle.x()
            else:
                delta = pixel.y() if pixel.y() else angle.y()
            if not delta:
                return False
            if pixel.y() or pixel.x():
                step = int(-delta)
            else:
                step = int(-delta * 0.58)
            if not step:
                step = -1 if delta > 0 else 1
            base = self._targets.get(area)
            if base is None:
                base = bar.value()
            target = max(bar.minimum(), min(bar.maximum(), base + step))
            self._targets[area] = target
            if target == bar.value() and target == base:
                return True
            old = self._anims.get(area)
            if old is not None:
                try:
                    old.stop()
                except RuntimeError:
                    pass
            anim = QPropertyAnimation(bar, b'value', self)
            distance = abs(target - bar.value())
            anim.setDuration(max(120, min(300, int(distance * 1.4))))
            anim.setStartValue(bar.value())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            def finish(a=area, an=anim):
                if self._anims.get(a) is an:
                    self._anims.pop(a, None)
                    self._targets.pop(a, None)

            anim.finished.connect(finish)
            self._anims[area] = anim
            anim.start()
            return True
        except Exception:
            return False


