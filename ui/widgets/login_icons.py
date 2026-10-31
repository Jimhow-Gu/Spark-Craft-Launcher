'''登录方式小图标：LittleSkin / 离线 / 微软四色 logo。'''

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

GRAY = '#8f96a3'
_CACHE = {}


def _new_pixmap(size):
    dpr = 2
    pixmap = QPixmap(int(size) * dpr, int(size) * dpr)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(dpr)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    scale = float(size) / 16.0
    painter.scale(scale, scale)
    return pixmap, painter


def _draw_littleskin(painter):
    pen = QPen(QColor(GRAY), 1.5)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(QRectF(1.0, 2.5, 14.0, 11.0), 2.2, 2.2)
    painter.setPen(QPen(QColor(GRAY), 1.2))
    painter.drawLine(3.5, 6.2, 6.2, 6.2)
    painter.drawLine(3.5, 8.8, 6.2, 8.8)
    painter.setBrush(QColor(GRAY))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(QRectF(9.0, 4.8, 3.2, 3.2))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(GRAY), 1.5))
    painter.drawArc(QRectF(7.6, 8.2, 6.2, 5.2), 0, 180 * 16)


def _draw_offline(painter):
    pen = QPen(QColor(GRAY), 1.9)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QRectF(2.3, 2.3, 11.4, 11.4))
    painter.drawLine(4.6, 11.4, 11.4, 4.6)


def _draw_microsoft(painter):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor('#f25022'))
    painter.drawRect(QRectF(2.0, 2.0, 5.4, 5.4))
    painter.setBrush(QColor('#7fba00'))
    painter.drawRect(QRectF(8.6, 2.0, 5.4, 5.4))
    painter.setBrush(QColor('#00a4ef'))
    painter.drawRect(QRectF(2.0, 8.6, 5.4, 5.4))
    painter.setBrush(QColor('#ffb900'))
    painter.drawRect(QRectF(8.6, 8.6, 5.4, 5.4))


def _draw_thirdparty(painter):
    pen = QPen(QColor('#4da3ff'), 1.5)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QRectF(2.2, 2.2, 11.6, 11.6))
    painter.drawEllipse(QRectF(5.7, 2.2, 4.6, 11.6))
    painter.drawLine(2.5, 8.0, 13.5, 8.0)


def login_icon(kind: str, size: int = 14) -> QPixmap:
    kind = str(kind or 'offline').lower()
    if kind not in ('littleskin', 'offline', 'microsoft', 'thirdparty'):
        kind = 'thirdparty'
    key = (kind, int(size))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pixmap, painter = _new_pixmap(size)
    try:
        if kind == 'microsoft':
            _draw_microsoft(painter)
        elif kind == 'offline':
            _draw_offline(painter)
        elif kind == 'littleskin':
            _draw_littleskin(painter)
        else:
            _draw_thirdparty(painter)
    finally:
        painter.end()
    _CACHE[key] = pixmap
    return pixmap


__all__ = ['login_icon']
