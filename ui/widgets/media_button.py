'''侧边栏媒体控制按钮：显式跟随深色/浅色主题，避免白色半透明背景在浅色下不可见。'''

from __future__ import annotations

from PySide6.QtWidgets import QPushButton


class MediaButton(QPushButton):
    _DARK_STYLE = (
        'QPushButton { background:rgba(255,255,255,0.07);'
        ' border:1px solid rgba(255,255,255,0.16); border-radius:6px; }'
        'QPushButton:hover { background:rgba(255,255,255,0.16);'
        ' border-color:rgba(255,255,255,0.26); }'
        'QPushButton:pressed { background:rgba(255,255,255,0.22); }'
        'QPushButton:disabled { background:rgba(255,255,255,0.03);'
        ' border-color:rgba(255,255,255,0.07); }'
        'QToolTip, QToolTip QLabel { background-color:#20232b; color:#f0f0f0;'
        ' border:1px solid #3a4050; padding:4px 7px; }'
    )
    _LIGHT_STYLE = (
        'QPushButton { background:rgba(0,0,0,0.06);'
        ' border:1px solid rgba(0,0,0,0.20); border-radius:6px; }'
        'QPushButton:hover { background:rgba(0,0,0,0.12);'
        ' border-color:rgba(0,0,0,0.30); }'
        'QPushButton:pressed { background:rgba(0,0,0,0.18); }'
        'QPushButton:disabled { background:rgba(0,0,0,0.03);'
        ' border-color:rgba(0,0,0,0.09); }'
        'QToolTip, QToolTip QLabel { background-color:#fbfbfb; color:#1b1b1b;'
        ' border:1px solid #c8ccd2; padding:4px 7px; }'
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._light = False
        # 样式完全由 set_light 控制，阻止 ThemeManager 再次做颜色转换。
        self.setProperty('_theme_ignore', True)
        self._apply_style()

    def set_light(self, light: bool):
        light = bool(light)
        if light == self._light:
            return
        self._light = light
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(self._LIGHT_STYLE if self._light else self._DARK_STYLE)


__all__ = ['MediaButton']
