'''自绘小图标，避免依赖 emoji 字体。'''

from __future__ import annotations
import math
from typing import Dict, Tuple
from PySide6.QtCore import (QPointF, QRectF, Qt)
from PySide6.QtGui import (QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
_CACHE: 'Dict[Tuple[str, int, str, float], QIcon]' = {}

def _new_painter(size):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.scale(size / 24.0, size / 24.0)
    return pixmap, painter

def _pen(color, width=1.8):
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen

def _draw_play(painter, color, stroke):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    path = QPainterPath()
    path.moveTo(8, 5)
    path.lineTo(19, 12)
    path.lineTo(8, 19)
    path.closeSubpath()
    painter.drawPath(path)

def _draw_pause(painter, color, stroke):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(7, 5, 3.6, 14), 1.2, 1.2)
    painter.drawRoundedRect(QRectF(13.4, 5, 3.6, 14), 1.2, 1.2)

def _draw_stop(painter, color, stroke):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(6.5, 6.5, 11, 11), 2.0, 2.0)

def _draw_download(painter, color, stroke):
    painter.setPen(_pen(color, stroke))
    painter.setBrush(Qt.NoBrush)
    painter.drawLine(QPointF(12, 4), QPointF(12, 15))
    path = QPainterPath()
    path.moveTo(7, 10)
    path.lineTo(12, 15)
    path.lineTo(17, 10)
    painter.drawPath(path)
    painter.drawLine(QPointF(5, 19), QPointF(19, 19))

def _draw_components(painter, color, stroke):
    painter.setPen(_pen(color, stroke))
    painter.setBrush(Qt.NoBrush)
    for x, y in ((5, 5), (13, 5), (5, 13), (13, 13)):
        painter.drawRoundedRect(QRectF(x, y, 6, 6), 1.4, 1.4)

def _draw_package(painter, color, stroke):
    painter.setPen(_pen(color, stroke))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(QRectF(5, 6, 14, 13), 1.5, 1.5)
    painter.drawLine(QPointF(5, 10), QPointF(19, 10))
    painter.drawLine(QPointF(12, 6), QPointF(12, 19))
    painter.drawLine(QPointF(8, 4.5), QPointF(12, 7))
    painter.drawLine(QPointF(16, 4.5), QPointF(12, 7))

def _draw_grid(painter, color, stroke):
    painter.setPen(_pen(color, stroke))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(QRectF(3.5, 3.5, 7, 7), 1.4, 1.4)
    painter.drawRoundedRect(QRectF(13.5, 3.5, 7, 7), 1.4, 1.4)
    painter.drawRoundedRect(QRectF(3.5, 13.5, 7, 7), 1.4, 1.4)
    painter.drawRoundedRect(QRectF(13.5, 13.5, 7, 7), 1.4, 1.4)


def _draw_export(painter, color, stroke):
    painter.setPen(_pen(color, stroke))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(QRectF(4.5, 9, 15, 10.5), 1.5, 1.5)
    painter.drawLine(QPointF(12, 3.5), QPointF(12, 12.5))
    painter.drawLine(QPointF(8.5, 7), QPointF(12, 3.5))
    painter.drawLine(QPointF(15.5, 7), QPointF(12, 3.5))


def _draw_database(painter, color, stroke):
    painter.setPen(_pen(color, stroke))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QRectF(6, 4.5, 12, 4))
    painter.drawLine(QPointF(6, 6.5), QPointF(6, 17.5))
    painter.drawLine(QPointF(18, 6.5), QPointF(18, 17.5))
    painter.drawArc(QRectF(6, 15.5, 12, 4), 180 * 16, 180 * 16)

def _draw_palette(painter, color, stroke):
    painter.setPen(_pen(color, stroke))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QRectF(5, 5, 14, 14))
    painter.setBrush(QColor(color))
    for x, y in ((9, 9), (14, 9), (9, 14)):
        painter.drawEllipse(QPointF(x, y), 1.2, 1.2)

def _draw_sun(painter, color, stroke):
    painter.setPen(_pen(color, stroke))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QPointF(12, 12), 4.2, 4.2)
    for dx, dy in ((0, -8.5), (0, 8.5), (-8.5, 0), (8.5, 0), (-6, -6), (6, -6), (-6, 6), (6, 6)):
        painter.drawLine(QPointF(12 + dx * 0.65, 12 + dy * 0.65), QPointF(12 + dx, 12 + dy))

def _draw_music_note(painter, color, stroke, muted=False):
    painter.setPen(_pen(color, max(1.6, stroke)))
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(6.0, 15.0, 5.4, 4.0))
    painter.drawLine(QPointF(11.1, 17.0), QPointF(11.1, 5.1))
    path = QPainterPath()
    path.moveTo(11.1, 5.1)
    path.cubicTo(14.0, 4.6, 17.4, 6.2, 17.4, 10.1)
    path.cubicTo(16.2, 8.4, 13.8, 8.0, 11.1, 8.9)
    painter.drawPath(path)
    if muted:
        painter.setPen(_pen('#ff7b72', max(2.0, stroke + 0.4)))
        painter.drawLine(QPointF(4.2, 4.2), QPointF(19.8, 19.8))

def _draw_shirt(painter, color, stroke):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    path = QPainterPath()
    path.moveTo(9.0, 4.0)
    path.lineTo(15.0, 4.0)
    path.lineTo(20.2, 6.8)
    path.lineTo(18.4, 11.2)
    path.lineTo(16.0, 10.0)
    path.lineTo(16.0, 20.0)
    path.lineTo(8.0, 20.0)
    path.lineTo(8.0, 10.0)
    path.lineTo(5.6, 11.2)
    path.lineTo(3.8, 6.8)
    path.closeSubpath()
    painter.drawPath(path)

def _draw_pencil(painter, color, stroke):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    path = QPainterPath()
    path.moveTo(4.2, 19.8)
    path.lineTo(6.2, 13.4)
    path.lineTo(15.8, 3.8)
    path.lineTo(20.2, 8.2)
    path.lineTo(10.6, 17.8)
    path.closeSubpath()
    painter.drawPath(path)
    painter.setBrush(QColor('#f0f0f0'))
    tip = QPolygonF([QPointF(6.2, 13.4), QPointF(10.6, 17.8), QPointF(4.2, 19.8)])
    painter.drawPolygon(tip)

def _draw_switch(painter, color, stroke):
    painter.setPen(_pen(color, max(1.8, stroke)))
    painter.setBrush(Qt.NoBrush)
    painter.drawLine(QPointF(4.0, 8.6), QPointF(19.0, 8.6))
    painter.drawLine(QPointF(19.0, 8.6), QPointF(15.4, 5.0))
    painter.drawLine(QPointF(19.0, 8.6), QPointF(15.4, 12.2))
    painter.drawLine(QPointF(20.0, 15.4), QPointF(5.0, 15.4))
    painter.drawLine(QPointF(5.0, 15.4), QPointF(8.6, 11.8))
    painter.drawLine(QPointF(5.0, 15.4), QPointF(8.6, 19.0))

def _draw_cape(painter, color, stroke):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    path = QPainterPath()
    path.moveTo(8.0, 4.0)
    path.cubicTo(10.0, 6.0, 14.0, 6.0, 16.0, 4.0)
    path.lineTo(19.4, 7.0)
    path.lineTo(18.2, 20.0)
    path.cubicTo(14.0, 17.0, 10.0, 17.0, 5.8, 20.0)
    path.lineTo(4.6, 7.0)
    path.closeSubpath()
    painter.drawPath(path)

def _draw_close(painter, color, stroke):
    painter.setPen(_pen(color, max(1.9, stroke)))
    painter.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
    painter.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))

def _draw_chevron_right(painter, color, stroke):
    painter.setPen(_pen(color, max(1.9, stroke)))
    painter.drawLine(QPointF(9.0, 5.0), QPointF(15.5, 12.0))
    painter.drawLine(QPointF(15.5, 12.0), QPointF(9.0, 19.0))

def _draw_chevron_down(painter, color, stroke):
    painter.setPen(_pen(color, max(1.9, stroke)))
    painter.drawLine(QPointF(5.0, 9.0), QPointF(12.0, 15.5))
    painter.drawLine(QPointF(12.0, 15.5), QPointF(19.0, 9.0))

def _draw_chevron_up(painter, color, stroke):
    painter.setPen(_pen(color, max(1.9, stroke)))
    painter.drawLine(QPointF(5.0, 15.5), QPointF(12.0, 9.0))
    painter.drawLine(QPointF(12.0, 9.0), QPointF(19.0, 15.5))

def _draw_heart(painter, color, stroke, filled=False):
    path = QPainterPath()
    path.moveTo(12, 20)
    path.cubicTo(10.5, 18.5, 4, 14.2, 4, 9.2)
    path.cubicTo(4, 6.2, 6.2, 4.5, 8.6, 4.5)
    path.cubicTo(10.2, 4.5, 11.3, 5.3, 12, 6.4)
    path.cubicTo(12.7, 5.3, 13.8, 4.5, 15.4, 4.5)
    path.cubicTo(17.8, 4.5, 20, 6.2, 20, 9.2)
    path.cubicTo(20, 14.2, 13.5, 18.5, 12, 20)
    path.closeSubpath()
    if filled:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPath(path)
        return
    painter.setPen(_pen(color, max(1.8, stroke)))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)

def _draw_trash(painter, color, stroke):
    painter.setPen(_pen(color, max(1.7, stroke)))
    painter.setBrush(Qt.NoBrush)
    painter.drawLine(QPointF(5, 7), QPointF(19, 7))
    painter.drawLine(QPointF(9, 7), QPointF(9, 4.5))
    painter.drawLine(QPointF(9, 4.5), QPointF(15, 4.5))
    painter.drawLine(QPointF(15, 4.5), QPointF(15, 7))
    path = QPainterPath()
    path.moveTo(6.5, 8)
    path.lineTo(7.3, 19)
    path.lineTo(16.7, 19)
    path.lineTo(17.5, 8)
    painter.drawPath(path)
    painter.drawLine(QPointF(10, 10.5), QPointF(10.2, 16.5))
    painter.drawLine(QPointF(14, 10.5), QPointF(13.8, 16.5))

def _draw_info(painter, color, stroke):
    painter.setPen(_pen(color, max(2.0, stroke + 0.2)))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QPointF(12, 12), 8.6, 8.6)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(QRectF(10.9, 10.4, 2.2, 7.6), 1.1, 1.1)
    painter.drawRoundedRect(QRectF(10.9, 5.8, 2.2, 2.4), 1.1, 1.1)

def _draw_gear(painter, color, stroke):
    painter.save()
    painter.translate(12, 12)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    for angle in range(0, 360, 45):
        painter.save()
        painter.rotate(angle)
        painter.drawRoundedRect(QRectF(-1.7, -10.2, 3.4, 4.2), 0.8, 0.8)
        painter.restore()
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(color, max(2.2, stroke + 0.4)))
    painter.drawEllipse(QPointF(0, 0), 6.2, 6.2)
    painter.restore()

_LIGHT_MODE = False


def set_light_mode(light: bool):
    global _LIGHT_MODE
    _LIGHT_MODE = bool(light)


def _light_color(color):
    qcolor = QColor(color)
    if not qcolor.isValid() or qcolor.lightness() <= 145:
        return color
    if max(qcolor.red(), qcolor.green(), qcolor.blue()) - min(
        qcolor.red(), qcolor.green(), qcolor.blue()
    ) > 28:
        return QColor(
            int(qcolor.red() * 0.62),
            int(qcolor.green() * 0.62),
            int(qcolor.blue() * 0.62),
            qcolor.alpha(),
        )
    return QColor(45, 50, 62, qcolor.alpha())


def set_button_icon(button, name, size=20, color='#e6edf3', stroke=1.8):
    button._painted_icon_spec = (name, size, color, stroke)
    button.setIcon(painted_icon(name, size, color, stroke))


def refresh_button_icon(button):
    spec = getattr(button, '_painted_icon_spec', None)
    if spec is None:
        return False
    try:
        button.setIcon(painted_icon(*spec))
    except RuntimeError:
        return False
    return True


def painted_icon(name, size=20, color='#e6edf3', stroke=1.8, light_safe=False):
    '''返回自绘 QIcon；name 支持 play/pause/music/music_off/shirt/pencil/switch/cape/close。

    light_safe=True 表示颜色已经是为当前主题选好的，不再做浅色换算。
    '''

    if _LIGHT_MODE and not light_safe:
        color = _light_color(color)
    key = str(name), int(size), str(color), float(stroke), bool(light_safe)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pixmap, painter = _new_painter(size)
    try:
        if name == 'play':
            _draw_play(painter, color, stroke)
        elif name == 'stop':
            _draw_stop(painter, color, stroke)
        elif name == 'download':
            _draw_download(painter, color, stroke)
        elif name == 'components':
            _draw_components(painter, color, stroke)
        elif name == 'package':
            _draw_package(painter, color, stroke)
        elif name == 'database':
            _draw_database(painter, color, stroke)
        elif name == 'palette':
            _draw_palette(painter, color, stroke)
        elif name == 'sun':
            _draw_sun(painter, color, stroke)
        elif name == 'pause':
            _draw_pause(painter, color, stroke)
        elif name == 'music':
            _draw_music_note(painter, color, stroke, muted=False)
        elif name == 'music_off':
            _draw_music_note(painter, color, stroke, muted=True)
        elif name == 'shirt':
            _draw_shirt(painter, color, stroke)
        elif name == 'pencil':
            _draw_pencil(painter, color, stroke)
        elif name == 'switch':
            _draw_switch(painter, color, stroke)
        elif name == 'cape':
            _draw_cape(painter, color, stroke)
        elif name == 'close':
            _draw_close(painter, color, stroke)
        elif name == 'chevron_right':
            _draw_chevron_right(painter, color, stroke)
        elif name == 'chevron_down':
            _draw_chevron_down(painter, color, stroke)
        elif name == 'chevron_up':
            _draw_chevron_up(painter, color, stroke)
        elif name == 'heart':
            _draw_heart(painter, color, stroke, filled=False)
        elif name == 'heart_filled':
            _draw_heart(painter, color, stroke, filled=True)
        elif name == 'trash':
            _draw_trash(painter, color, stroke)
        elif name == 'gear':
            _draw_gear(painter, color, stroke)
        elif name == 'info':
            _draw_info(painter, color, stroke)
        elif name == 'export':
            _draw_export(painter, color, stroke)
        elif name == 'grid':
            _draw_grid(painter, color, stroke)
    finally:
        painter.end()
    icon = QIcon(pixmap)
    _CACHE[key] = icon
    return icon

