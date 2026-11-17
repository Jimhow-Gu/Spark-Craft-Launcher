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
        # 结束帧用同步 repaint，确保透明卡片缩小后旧内容立即被擦掉；
        # 再用两个延迟刷新覆盖 finished 回调里才执行 hide/setMaximumHeight 的情况。
        self._final_refresh()
        QTimer.singleShot(0, self._final_refresh)
        QTimer.singleShot(24, self._final_refresh)

    def _final_refresh(self):
        try:
            self._refresh_area(force=True)
        except RuntimeError:
            pass

    def _refresh_area(self, *_args, force=False):
        try:
            target = self._target_widget
        except RuntimeError:
            return
        widget = target
        area = None
        try:
            target.updateGeometry()
            target.update()
            # 依次激活 section、分组容器和滚动内容布局，避免下一组标题
            # 还停留在动画前的位置。透明背景下只 updateGeometry 不够，
            # 父级也必须标记重绘，否则会留下旧高度的卡片背景。
            for _ in range(10):
                parent = widget.parentWidget()
                if parent is None:
                    break
                parent.updateGeometry()
                parent.update()
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
                if force:
                    area.viewport().repaint()
                    # 设置页叠在半透明背景/视频上，只重绘 viewport 可能
                    # 不会清掉顶层的旧像素；最后再刷新一次顶层窗口。
                    try:
                        area.window().update()
                    except RuntimeError:
                        pass
                else:
                    area.viewport().update()
            elif target.parentWidget() is not None:
                if force:
                    target.parentWidget().repaint()
                else:
                    target.parentWidget().update()
        except RuntimeError:
            pass


__all__ = ['HeightAnimation']
