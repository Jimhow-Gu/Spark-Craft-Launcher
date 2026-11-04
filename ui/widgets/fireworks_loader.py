'''透明背景的烟花加载动画。'''

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QMovie, QPainter
from PySide6.QtWidgets import QWidget

_LOADING_GIF = Path(__file__).resolve().parent.parent / 'imgs' / 'Loading_small.gif'


class FireworksLoader(QWidget):
    '''按物理像素缩放的烟花加载动画，避免发糊。'''

    def __init__(self, size=56, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._movie = None
        if _LOADING_GIF.is_file():
            self._movie = QMovie(str(_LOADING_GIF), parent=self)
            self._movie.setCacheMode(QMovie.CacheAll)
            self._movie.frameChanged.connect(self.update)
            self._movie.start()
            self._movie.jumpToFrame(6)

    def showEvent(self, event):
        super().showEvent(event)
        if self._movie is not None and self._movie.state() != QMovie.Running:
            self._movie.start()
            self._movie.jumpToFrame(6)

    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)

    def stop(self):
        if self._movie is not None:
            self._movie.stop()

    def paintEvent(self, _event):
        if self._movie is None:
            return
        pixmap = self._movie.currentPixmap()
        if pixmap.isNull():
            return
        try:
            dpr = max(1.0, float(self.devicePixelRatioF()))
        except Exception:
            dpr = 1.0
        target = QSize(
            max(1, int(round(self.width() * dpr))),
            max(1, int(round(self.height() * dpr))),
        )
        scaled = pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        painter = QPainter(self)
        x = (self.width() - scaled.width() / dpr) / 2
        y = (self.height() - scaled.height() / dpr) / 2
        painter.drawPixmap(int(round(x)), int(round(y)), scaled)
        painter.end()
