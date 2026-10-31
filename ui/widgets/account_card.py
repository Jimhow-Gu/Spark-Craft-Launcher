'''首页账户卡片：MC 头像 + 昵称 + 登录模式。'''

from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from ui.widgets.login_icons import login_icon

class AccountCard(QFrame):
    clicked = Signal()
    avatar_failed = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        from ui.pages.account_page import AvatarLabel
        self.setObjectName('AccountCard')
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('\n            QFrame#AccountCard { background:rgba(255,255,255,0.055);\n                                 border:none; border-radius:0; }\n            QFrame#AccountCard:hover { background:rgba(255,255,255,0.11); }\n        ')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)
        self.avatar = AvatarLabel(self, size=42, radius=9)
        self.avatar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.avatar.all_failed.connect(self.avatar_failed)
        layout.addWidget(self.avatar)
        self._text_panel = QWidget(self)
        self._text_panel.setStyleSheet('background:transparent;')
        self._text_panel.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_layout = QVBoxLayout(self._text_panel)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        self.name_label = QLabel('未设置昵称')
        self.name_label.setStyleSheet('color:#f0f0f0; font-size:14px; background:transparent;')
        self.name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.mode_label = QLabel('离线登录')
        self.mode_label.setStyleSheet('color:rgba(255,255,255,0.52); font-size:11px; background:transparent;')
        self.mode_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.mode_icon = QLabel()
        self.mode_icon.setFixedSize(14, 14)
        self.mode_icon.setStyleSheet('background:transparent;')
        self.mode_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.mode_row = QHBoxLayout()
        self.mode_row.setContentsMargins(0, 0, 0, 0)
        self.mode_row.setSpacing(3)
        self.mode_row.addWidget(self.mode_icon)
        self.mode_row.addWidget(self.mode_label)
        self.mode_row.addStretch(1)
        text_layout.addWidget(self.name_label)
        text_layout.addLayout(self.mode_row)
        layout.addWidget(self._text_panel, 1)
        self._layout = layout
        self._text_layout = text_layout
        self._compact = False
        self._mode_text = ''
        self._mode_kind = 'offline'

    def set_compact(self, compact: bool = True, expanded_width: int = 210, animate: bool = True):
        compact = bool(compact)
        self._compact = compact
        if compact:
            self._text_panel.hide()
            self._layout.setContentsMargins(6, 6, 6, 6)
            self._layout.setSpacing(0)
            self._layout.setStretch(0, 0)
            self._layout.setStretch(1, 0)
            self.setFixedSize(54, 54)
            self.setStyleSheet(
                'QFrame#AccountCard { background:transparent; border:none; }'
                'QFrame#AccountCard:hover { background:rgba(255,255,255,0.08);'
                ' border-radius:27px; }'
            )
            return
        self._text_panel.show()
        has_mode = bool(self._mode_text)
        self.mode_label.setVisible(has_mode)
        self.mode_icon.setVisible(has_mode)
        self._layout.setContentsMargins(14, 6, 8, 6)
        self._layout.setSpacing(4)
        self._layout.setStretch(0, 0)
        self._layout.setStretch(1, 1)
        self.setFixedSize(max(120, int(expanded_width)), 54)
        self.setStyleSheet(
            'QFrame#AccountCard { background:rgba(255,255,255,0.07);'
            ' border:1px solid rgba(255,255,255,0.08); border-radius:8px; }'
            'QFrame#AccountCard:hover { background:rgba(255,255,255,0.12); }'
        )


    def set_avatar_only(self, avatar_only: bool = True):
        self.set_compact(bool(avatar_only))

    def set_account(
        self, name, mode, urls=None, skin_path=None, head_path=None,
        avatar_cache=None, mode_kind='offline',
    ):
        self.name_label.setText(name or '未登录')
        self._mode_text = str(mode or '')
        self._mode_kind = str(mode_kind or 'offline')
        self.mode_label.setText(self._mode_text)
        self.mode_icon.setPixmap(login_icon(self._mode_kind, 14))
        if not self._compact:
            self.mode_label.setVisible(bool(self._mode_text))
            self.mode_icon.setVisible(bool(self._mode_text))
        if head_path:
            self.avatar.set_head_file(head_path)
            return
        if skin_path:
            self.avatar.set_skin_file(skin_path)
            return
        if urls:
            self.avatar.set_image_url(urls, cache_path=avatar_cache)
            return
        steve_head = Path(__file__).resolve().parent.parent / 'imgs' / 'steve_head.png'
        if steve_head.is_file():
            self.avatar.set_head_file(str(steve_head))
            return
        self.avatar.setText(name or '?')

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


