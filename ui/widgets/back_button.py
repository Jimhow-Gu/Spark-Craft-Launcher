'''统一的圆形返回箭头按钮。'''

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QPushButton
from core.app_font import ui_font


class BackButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__('←', parent)
        self.setObjectName('BackButton')
        self.setFixedSize(38, 38)
        self.setFont(ui_font(17))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setAttribute(Qt.WA_Hover, True)
        # 自绘圆形，避免全局 QPushButton 的 6px 圆角或半透明背景
        # 在部分状态下把按钮画成方形。
        self.setStyleSheet(
            'QPushButton#BackButton { background:transparent; border:none; }'
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isDown():
            fill = QColor(136, 221, 221, 105)
            border = QColor(136, 221, 221, 190)
        elif self.underMouse():
            fill = QColor(136, 221, 221, 55)
            border = QColor(136, 221, 221, 150)
        else:
            fill = QColor(18, 23, 23, 150)
            border = QColor(255, 255, 255, 46)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1.0))
        painter.drawEllipse(rect)
        painter.setPen(QColor(240, 240, 240))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignCenter, '←')
        painter.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)
