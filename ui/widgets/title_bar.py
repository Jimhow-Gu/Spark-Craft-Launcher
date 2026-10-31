'''无边框主窗口的自定义标题栏。'''

from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QEasingCurve, QPropertyAnimation
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPixmap)
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton)

from core.app_font import ui_font

class WindowTitleBar(QFrame):
    HEIGHT = 56
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._drag_offset = QPoint()
        self._dragging = False
        self._title_text = ''
        self._light = False
        self._logo_pixmap = None
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName('WindowTitleBar')
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet('\n            QFrame#WindowTitleBar { background: transparent; border: none; }\n            QLabel { background:transparent; }\n        ')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 6, 0)
        self._layout = layout
        layout.setSpacing(6)
        logo_label = QLabel()
        logo_label.setFixedSize(22, 22)
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.logo_label = logo_label
        logo_label.hide()
        logo_path = Path(__file__).resolve().parent.parent / 'imgs' / 'sparkler_alpha.png'
        if not logo_path.is_file():
            logo_path = Path(__file__).resolve().parent.parent / 'imgs' / 'sparkler.png'
        if logo_path.is_file():
            try:
                from ui.widgets.settings_widgets import hidpi_pixmap, trim_transparent
                pixmap = trim_transparent(QPixmap(str(logo_path)))
                pixmap = hidpi_pixmap(pixmap, 22, 22, smooth=True, keep_aspect=True)
                if not pixmap.isNull():
                    self._logo_pixmap = pixmap
                    self._update_logo()
            except Exception:
                logo_label.clear()
        layout.addWidget(logo_label)
        layout.addSpacing(8)
        self.title_label = QLabel('Spark Craft Launcher')
        self.title_label.setFont(ui_font(13, QFont.DemiBold))
        self.title_label.setStyleSheet('color:#88DDDD; background:transparent;')
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._title_effect = QGraphicsOpacityEffect(self.title_label)
        self._title_effect.setOpacity(1.0)
        self.title_label.setGraphicsEffect(self._title_effect)
        self._title_anim = None
        layout.addWidget(self.title_label)
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
        self._update_logo()
        self.update()

    def _update_logo(self):
        '''浅色标题栏下把偏白的 logo 压暗，避免与浅色背景融在一起。'''

        pixmap = self._logo_pixmap
        if pixmap is None or pixmap.isNull():
            return
        if not self._light:
            self.logo_label.setPixmap(pixmap)
            return
        image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() <= 0:
                    continue
                high = max(color.red(), color.green(), color.blue())
                low = min(color.red(), color.green(), color.blue())
                if high - low <= 35 and high >= 120:
                    color.setRgb(52, 59, 72, color.alpha())
                elif high >= 190:
                    color.setRgb(
                        int(color.red() * 0.68),
                        int(color.green() * 0.68),
                        int(color.blue() * 0.68),
                        color.alpha(),
                    )
                image.setPixelColor(x, y, color)
        self.logo_label.setPixmap(QPixmap.fromImage(image))

    def set_left_margin(self, pixels: int):
        self._layout.setContentsMargins(int(pixels), 0, 6, 0)

    def set_title(self, text: str, color: str='#6db6ff'):
        self._title_text = text or 'Spark Craft Launcher'
        self.title_label.setPixmap(QPixmap())
        self.title_label.setText(self._title_text)
        self.title_label.setStyleSheet(
            f'color:{color}; background:transparent;'
        )
        self.title_label.show()

    def set_title_image(self, path: str):
        pixmap = QPixmap(str(path or ''))
        if pixmap.isNull():
            return False
        pixmap = pixmap.scaledToHeight(
            22, Qt.SmoothTransformation
        )
        self.title_label.setText('')
        self.title_label.setPixmap(pixmap)
        self.title_label.show()
        return True

    def set_title_visible(self, visible: bool, animate: bool=False):
        visible = bool(visible)
        if self._title_anim is not None:
            try:
                self._title_anim.stop()
            except RuntimeError:
                pass
            self._title_anim = None
        if not animate:
            self._title_effect.setOpacity(1.0 if visible else 0.0)
            self.title_label.setVisible(visible)
            return
        if visible:
            self.title_label.show()
        animation = QPropertyAnimation(
            self._title_effect, b'opacity', self
        )
        animation.setDuration(120 if visible else 90)
        animation.setStartValue(self._title_effect.opacity())
        animation.setEndValue(1.0 if visible else 0.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.valueChanged.connect(
            lambda _value: (self.title_label.update(), self.update())
        )
        if not visible:
            animation.finished.connect(
                lambda: self.title_label.hide()
            )
        else:
            animation.finished.connect(
                lambda: self._title_effect.setOpacity(1.0)
            )
        self._title_anim = animation
        animation.start()

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
