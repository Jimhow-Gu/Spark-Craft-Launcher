'''统一的返回箭头按钮。'''

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from core.app_font import ui_font

class BackButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__('←', parent)
        self.setFixedSize(38, 38)
        self.setFont(ui_font(17))
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            'QPushButton { background:rgba(18,23,23,0.55); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.18); border-radius:18px; }'
            'QPushButton:hover { background:rgba(136,221,221,0.22);'
            ' border-color:rgba(136,221,221,0.55); border-radius:18px; }'
            'QPushButton:pressed { background:rgba(136,221,221,0.32);'
            ' border-radius:18px; }'
        )


