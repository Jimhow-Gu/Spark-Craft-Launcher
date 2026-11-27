"""统一圆角列表、淡入与展开/收起动画。"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPropertyAnimation, Qt,
)
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QGraphicsOpacityEffect, QListWidget, QVBoxLayout,
    QWidget,
)

from ui.widgets.rounded_combo import RoundedComboBox

LIST_QSS = """
QListWidget {
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 12px;
    padding: 6px;
    outline: none;
}
QListWidget::item {
    border-radius: 8px;
    padding: 0px 10px;
    margin: 2px 4px;
    color: palette(text);
}
QListWidget::item:hover {
    background: rgba(136, 221, 221, 0.08);
}
QListWidget::item:selected {
    background: rgba(136, 221, 221, 0.14);
    border: 1px solid rgba(136, 221, 221, 0.55);
    color: palette(text);
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 2px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.18);
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 0.28);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
"""


def apply_list_style(widget):
    if widget is None:
        return
    try:
        light = (
            widget.palette().color(QPalette.Window).lightness() > 128
        )
    except Exception:
        light = False
    handle = 'rgba(0, 0, 0, 0.16)' if light else 'rgba(255, 255, 255, 0.18)'
    hover = 'rgba(0, 0, 0, 0.24)' if light else 'rgba(255, 255, 255, 0.28)'
    widget.setStyleSheet(
        LIST_QSS.replace(
            'rgba(255, 255, 255, 0.18)', handle
        ).replace(
            'rgba(255, 255, 255, 0.28)', hover
        )
    )


class AnimatedListView(QListWidget):
    """统一圆角列表；首次显示或筛选替换时可调用 fade_in()。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('_theme_ignore', True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSpacing(4)
        self._applying_list_style = False
        self._list_light = False
        self._custom_style = ''
        self._apply_style()
        self._fade_anim = None
        self._fade_effect = None

    def _apply_style(self):
        if self._applying_list_style:
            return
        self._applying_list_style = True
        try:
            if self._custom_style:
                self.setStyleSheet(self._custom_style)
            else:
                apply_list_style(self)
        finally:
            self._applying_list_style = False

    def set_custom_style(self, style):
        self._custom_style = str(style or '')
        self._apply_style()

    def set_light(self, light):
        light = bool(light)
        if light == self._list_light:
            return
        self._list_light = light
        self._apply_style()

    def fade_in(self, duration=120):
        try:
            if not self.isVisible():
                return
            self._clear_fade()
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.0)
            self.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b'opacity', self)
            animation.setDuration(max(60, int(duration)))
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.finished.connect(self._clear_fade)
            self._fade_effect = effect
            self._fade_anim = animation
            animation.start()
        except RuntimeError:
            pass

    def _clear_fade(self):
        animation, effect = self._fade_anim, self._fade_effect
        self._fade_anim = None
        self._fade_effect = None
        if animation is not None:
            try:
                animation.stop()
            except RuntimeError:
                pass
        if effect is not None:
            try:
                self.setGraphicsEffect(None)
            except RuntimeError:
                pass

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self._apply_style()
        super().changeEvent(event)

    def hideEvent(self, event):
        self._clear_fade()
        super().hideEvent(event)


class AnimatedListContainer(QWidget):
    """可展开/收起的列表容器：160ms 高度 + 透明度，最大 320px。"""

    def __init__(self, content=None, parent=None, max_height=320):
        super().__init__(parent)
        self._max_height = max(80, int(max_height))
        self._expanded = True
        self._anim = None
        self._effect = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._content = None
        if content is not None:
            self.set_content(content)

    def set_content(self, content):
        if self._content is content:
            return
        if self._content is not None:
            self._layout.removeWidget(self._content)
        self._content = content
        if content is not None:
            self._layout.addWidget(content)
            content.setVisible(self._expanded)

    def content(self):
        return self._content

    def is_expanded(self):
        return self._expanded

    def set_expanded(self, expanded, animate=True):
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        content = self._content
        if content is None:
            return
        if not animate or not self.isVisible():
            content.setMaximumHeight(16777215 if expanded else 0)
            content.setVisible(expanded)
            return
        self._stop_anim()
        start = content.height() if content.isVisible() else 0
        target = min(
            self._max_height,
            max(1, content.sizeHint().height()),
        )
        if not expanded:
            start = max(start, content.height())
            target = 0
        content.show()
        effect = QGraphicsOpacityEffect(content)
        effect.setOpacity(1.0 if not expanded else 0.0)
        content.setGraphicsEffect(effect)
        height_anim = QPropertyAnimation(content, b'maximumHeight', self)
        height_anim.setDuration(160)
        height_anim.setStartValue(start)
        height_anim.setEndValue(target)
        height_anim.setEasingCurve(QEasingCurve.OutCubic)
        fade = QPropertyAnimation(effect, b'opacity', self)
        fade.setDuration(160)
        fade.setStartValue(1.0 if not expanded else 0.0)
        fade.setEndValue(0.0 if not expanded else 1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        height_anim.finished.connect(
            lambda: self._finish_anim(content, expanded)
        )
        self._anim = (height_anim, fade)
        self._effect = effect
        height_anim.start()
        fade.start()

    def _finish_anim(self, content, expanded):
        content.setMaximumHeight(16777215 if expanded else 0)
        content.setVisible(expanded)
        try:
            content.setGraphicsEffect(None)
        except RuntimeError:
            pass
        self._effect = None
        self._anim = None

    def _stop_anim(self):
        animation, effect = self._anim, self._effect
        self._anim = None
        self._effect = None
        if animation is not None:
            for item in animation:
                try:
                    item.stop()
                except RuntimeError:
                    pass
        if effect is not None and self._content is not None:
            try:
                self._content.setGraphicsEffect(None)
            except RuntimeError:
                pass

    def toggle(self):
        self.set_expanded(not self._expanded)

    def hideEvent(self, event):
        self._stop_anim()
        super().hideEvent(event)


class AnimatedPopupCombo(RoundedComboBox):
    """兼容统一入口；弹层动画由 RoundedComboBox 统一提供。"""

    pass


__all__ = [
    'AnimatedListContainer', 'AnimatedListView', 'AnimatedPopupCombo',
    'LIST_QSS', 'apply_list_style',
]
