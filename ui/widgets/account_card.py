'''首页账户卡片：MC 头像 + 昵称 + 登录模式。'''

from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QVBoxLayout)

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
        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        self.name_label = QLabel('未设置昵称')
        self.name_label.setStyleSheet('color:#f0f0f0; font-size:14px; background:transparent;')
        self.name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.mode_label = QLabel('离线登录')
        self.mode_label.setStyleSheet('color:rgba(255,255,255,0.52); font-size:11px; background:transparent;')
        self.mode_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_layout.addWidget(self.name_label)
        text_layout.addWidget(self.mode_label)
        layout.addLayout(text_layout, 1)

    def set_account(self, name, mode, urls=None, skin_path=None, head_path=None, avatar_cache=None):
        self.name_label.setText(name or '未登录')
        self.mode_label.setText(mode or '')
        self.mode_label.setVisible(bool(mode))
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


