'''新闻页共用的图片和加载占位控件。'''

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.widgets.fireworks_loader import FireworksLoader


class NewsImageLabel(QWidget):
    '''按完整宽度铺满新闻图；加载时显示动画 + 文字，不使用背景或大遮罩。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self.setFixedHeight(180)
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._placeholder = QWidget(self)
        self._placeholder.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._placeholder.setStyleSheet('background: transparent;')
        layout = QHBoxLayout(self._placeholder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addStretch(1)
        self._loading_icon = FireworksLoader(56)
        self._loading_text = QLabel('正在加载新闻图片')
        self._loading_text.setStyleSheet('color:#e3fcfc; font-size:14px; font-weight:600;')
        layout.addWidget(self._loading_icon, 0, Qt.AlignVCenter)
        layout.addWidget(self._loading_text, 0, Qt.AlignVCenter)
        layout.addStretch(1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._placeholder.setGeometry(self.rect())

    def set_source(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        self._source = pixmap
        self._placeholder.hide()
        self.update()

    def setText(self, text):
        self._source = None
        self._placeholder.show()
        show_icon = text in ('', '正在加载新闻图片', '正在加载新闻图片...')
        self._loading_icon.setVisible(show_icon)
        self._loading_text.setText('正在加载新闻图片' if show_icon else str(text))
        self.update()

    def stop(self):
        self._loading_icon.stop()

    def set_loading_text(self, text):
        '''保持烟花动画，只替换加载提示文字。'''

        self._source = None
        self._placeholder.show()
        self._loading_icon.show()
        self._loading_text.setText(str(text))
        self.update()

    def paintEvent(self, event):
        if self._source is None or self._source.isNull():
            return
        painter = QPainter(self)
        target = self.rect()
        source = self._source
        if source.width() <= 0 or source.height() <= 0:
            painter.end()
            return
        scale = max(target.width() / source.width(), target.height() / source.height())
        crop_w = max(1, int(target.width() / scale))
        crop_h = max(1, int(target.height() / scale))
        crop_x = max(0, (source.width() - crop_w) // 2)
        crop_y = max(0, (source.height() - crop_h) // 2)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(
            target,
            source,
            source.rect().adjusted(
                crop_x,
                crop_y,
                -(source.width() - crop_x - crop_w),
                -(source.height() - crop_y - crop_h),
            ),
        )
        painter.end()


class TranslationPending(QWidget):
    '''新闻正文尚未翻译时显示清晰的加载动画。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background: transparent;')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 20)
        layout.setSpacing(7)
        layout.addWidget(FireworksLoader(72), 0, Qt.AlignHCenter)
        label = QLabel('翻译施工中')
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet('color:#e3fcfc; font-size:15px; font-weight:700;')
        layout.addWidget(label)
