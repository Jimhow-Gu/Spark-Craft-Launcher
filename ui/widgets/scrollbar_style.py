'''统一重绘滚动条，避免各页面默认样式和透底纹理。'''

from __future__ import annotations
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QScrollBar
_SCROLLBAR_STYLE = """
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px 0 2px 0;
    border: none;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: rgba(136,221,221,0.30);
    min-height: 34px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(136,221,221,0.56);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px; width: 0px; background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0px 0 2px 0;
    border: none;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: rgba(136,221,221,0.30);
    min-width: 34px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background: rgba(136,221,221,0.56);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0px; width: 0px; background: transparent;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
"""
_LIGHT_SCROLLBAR_STYLE = """
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px 0 2px 0;
    border: none;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: rgba(64,153,153,0.30);
    min-height: 34px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(64,153,153,0.48);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px; width: 0px; background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0px 0 2px 0;
    border: none;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: rgba(64,153,153,0.30);
    min-width: 34px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background: rgba(64,153,153,0.48);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0px; width: 0px; background: transparent;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
"""


def _style_scrollbar(scrollbar: QScrollBar, light: bool):
    try:
        # 先标记忽略，再设置样式：setStyleSheet 会同步触发 StyleChange，
        # ThemeManager 若先于标记执行会把滚动条样式当成页面 QSS 清空。
        scrollbar.setProperty('_scl_scrollbar_styled', True)
        scrollbar.setProperty('_theme_ignore', True)
        scrollbar.setStyleSheet(
            _LIGHT_SCROLLBAR_STYLE if light else _SCROLLBAR_STYLE
        )
    except RuntimeError:
        pass


class ScrollBarStyler(QObject):
    '''在滚动条显示时统一替换样式（包括运行时动态创建的列表）。'''

    _light = False

    def eventFilter(self, obj, event):
        if isinstance(obj, QScrollBar) and event.type() == QEvent.Show:
            _style_scrollbar(obj, ScrollBarStyler._light)
        return False

    @classmethod
    def set_light_mode(cls, light: bool, visible_only: bool = False):
        cls._light = bool(light)
        app = QApplication.instance()
        if app is None:
            return
        for widget in app.allWidgets():
            if isinstance(widget, QScrollBar) and (
                not visible_only or widget.isVisible()
            ):
                _style_scrollbar(widget, cls._light)


