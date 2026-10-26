'''无边框主窗口的自定义标题栏。'''

from pathlib import Path

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPixmap)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton)

class WindowTitleBar(QFrame):
    HEIGHT = 38
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._drag_offset = QPoint()
        self._dragging = False
        self._title_text = ''
        self._light = False
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName('WindowTitleBar')
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet('\n            QFrame#WindowTitleBar { background: transparent; border: none; }\n            QLabel { background:transparent; }\n        ')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 6, 0)
        layout.setSpacing(6)
        logo_label = QLabel()
        logo_label.setFixedSize(22, 22)
        logo_label.setAlignment(Qt.AlignCenter)
        logo_path = Path(__file__).resolve().parent.parent / 'imgs' / 'sparkler_alpha.png'
        if not logo_path.is_file():
            logo_path = Path(__file__).resolve().parent.parent / 'imgs' / 'sparkler.png'
        if logo_path.is_file():
            try:
                pixmap = QPixmap(str(logo_path)).scaled(
                    22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                if not pixmap.isNull():
                    logo_label.setPixmap(pixmap)
            except Exception:
                logo_label.clear()
        layout.addWidget(logo_label)
        layout.addSpacing(2)
        layout.addStretch(1)
        self.min_btn = QPushButton('—')
        self.min_btn.setFixedSize(38, 28)
        self.min_btn.setToolTip('最小化')
        self.min_btn.setStyleSheet('QPushButton { background:transparent; color:#d7d7d7; border:none; border-radius:6px; font-size:15px; }QPushButton:hover { background:rgba(255,255,255,0.12); }')
        self.min_btn.clicked.connect(self._window.showMinimized)
        layout.addWidget(self.min_btn)
        self.close_btn = QPushButton('✕')
        self.close_btn.setFixedSize(38, 28)
        self.close_btn.setToolTip('关闭')
        self.close_btn.setStyleSheet('QPushButton { background:transparent; color:#d7d7d7; border:none; border-radius:6px; font-size:13px; }QPushButton:hover { background:#c42b1c; color:white; }')
        self.close_btn.clicked.connect(self._window.close)
        layout.addWidget(self.close_btn)

    def paintEvent(self, event):
        '''自绘顶部圆角，避免 QSS 圆角产生锯齿。'''

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        radius = 14.0
        right = rect.right() + 1
        bottom = rect.bottom() + 1
        path = QPainterPath()
        path.moveTo(rect.left(), rect.top() + radius)
        path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
        path.lineTo(right - radius, rect.top())
        path.quadTo(right, rect.top(), right, rect.top() + radius)
        path.lineTo(right, bottom)
        path.lineTo(rect.left(), bottom)
        path.closeSubpath()
        painter.setPen(Qt.NoPen)
        bar_color = QColor(246, 247, 249, 200) if self._light else QColor(16, 18, 24, 150)
        painter.setBrush(bar_color)
        painter.drawPath(path)
        painter.fillRect(rect.left(), rect.bottom(), rect.width(), 1, bar_color)
        painter.end()

    def set_light(self, light: bool):
        self._light = bool(light)
        self.update()

    def set_title(self, text: str, color: str='#6db6ff'):
        self._title_text = text or 'PMGL'

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        event.accept()
