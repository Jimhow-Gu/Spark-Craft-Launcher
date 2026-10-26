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
        self.setStyleSheet('QPushButton { background:transparent; color:#f0f0f0; border:none; border-radius:19px; }QPushButton:hover { background:rgba(255,255,255,0.1); }')


