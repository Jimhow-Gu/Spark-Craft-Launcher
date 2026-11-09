'''折叠面板高度动画：每帧刷新父级绘制区域，避免透明背景下留下残影。'''

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QTimer
from PySide6.QtWidgets import QAbstractScrollArea, QWidget


class HeightAnimation(QPropertyAnimation):
    '''动画修改 maximumHeight，并同步激活父级布局与重绘。

    Qt 默认把布局请求排到下一轮事件；动画帧间隔很短时，后面的分组标题
    会短暂停在旧位置，和正在展开的内容叠在一起，看起来就是残影/抖动。
    这里每帧主动激活父级布局，并刷新最近的滚动区域 viewport；结束帧再
    延迟刷新一次，覆盖各控件在 finished 回调里设置最终高度的情况。
    '''

    def __init__(self, target: QWidget, parent=None):
        super().__init__(target, b'maximumHeight', parent)
        self._target_widget = target
        self.valueChanged.connect(self._refresh_area)
        self.finished.connect(self._schedule_final_refresh)

    def _schedule_final_refresh(self):
        self._refresh_area()
        QTimer.singleShot(0, self._refresh_area)

    def _refresh_area(self, *_args):
        target = self._target_widget
        widget = target
        area = None
        try:
            target.updateGeometry()
            target.update()
            # 依次激活 section、分组容器和滚动内容布局，避免下一组标题
            # 还停留在动画前的位置。
            for _ in range(6):
                parent = widget.parentWidget()
                if parent is None:
                    break
                parent.updateGeometry()
                layout = parent.layout()
                if layout is not None:
                    try:
                        layout.activate()
                    except RuntimeError:
                        break
                if isinstance(parent, QAbstractScrollArea):
                    area = parent
                    break
                widget = parent
        except RuntimeError:
            return
        try:
            if area is not None:
                area.viewport().update()
            elif target.parentWidget() is not None:
                target.parentWidget().update()
        except RuntimeError:
            pass


__all__ = ['HeightAnimation']
