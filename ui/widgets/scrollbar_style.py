'''统一重绘滚动条，避免各页面默认样式和透底纹理。'''

from __future__ import annotations
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QScrollBar
_SCROLLBAR_STYLE = '\nQScrollBar:vertical {\n    background: #1a1c22;\n    width: 10px;\n    margin: 2px 2px 2px 0;\n    border: none;\n    border-radius: 5px;\n}\nQScrollBar::handle:vertical {\n    background: #6f7a8c;\n    min-height: 30px;\n    border-radius: 4px;\n}\nQScrollBar::handle:vertical:hover {\n    background: #8d9ab0;\n}\nQScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {\n    height: 0px; width: 0px; background: transparent;\n}\nQScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {\n    background: transparent;\n}\nQScrollBar:horizontal {\n    background: #1a1c22;\n    height: 10px;\n    margin: 0px 2px 2px 2px;\n    border: none;\n    border-radius: 5px;\n}\nQScrollBar::handle:horizontal {\n    background: #6f7a8c;\n    min-width: 30px;\n    border-radius: 4px;\n}\nQScrollBar::handle:horizontal:hover {\n    background: #8d9ab0;\n}\nQScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {\n    height: 0px; width: 0px; background: transparent;\n}\nQScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {\n    background: transparent;\n}\n'
_LIGHT_SCROLLBAR_STYLE = '\nQScrollBar:vertical {\n    background: #e6e8ec;\n    width: 10px;\n    margin: 2px 2px 2px 0;\n    border: none;\n    border-radius: 5px;\n}\nQScrollBar::handle:vertical {\n    background: #b8bfc9;\n    min-height: 30px;\n    border-radius: 4px;\n}\nQScrollBar::handle:vertical:hover {\n    background: #99a2af;\n}\nQScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {\n    height: 0px; width: 0px; background: transparent;\n}\nQScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {\n    background: transparent;\n}\nQScrollBar:horizontal {\n    background: #e6e8ec;\n    height: 10px;\n    margin: 0px 2px 2px 2px;\n    border: none;\n    border-radius: 5px;\n}\nQScrollBar::handle:horizontal {\n    background: #b8bfc9;\n    min-width: 30px;\n    border-radius: 4px;\n}\nQScrollBar::handle:horizontal:hover {\n    background: #99a2af;\n}\nQScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {\n    height: 0px; width: 0px; background: transparent;\n}\nQScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {\n    background: transparent;\n}\n'


def _style_scrollbar(scrollbar: QScrollBar, light: bool):
    try:
        scrollbar.setStyleSheet(
            _LIGHT_SCROLLBAR_STYLE if light else _SCROLLBAR_STYLE
        )
        scrollbar.setProperty('_scl_scrollbar_styled', True)
        scrollbar.setProperty('_theme_ignore', True)
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


