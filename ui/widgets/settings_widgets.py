'''设置页辅助控件：圆角图标绘制与 PCL 风格内存条。'''

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from core.app_font import ui_font


def trim_transparent(pixmap):
    '''裁掉四周全透明的留白，让图标的可见内容铺满目标尺寸。'''

    if pixmap is None or pixmap.isNull():
        return pixmap
    try:
        img = pixmap.toImage()
        if not img.hasAlphaChannel():
            return pixmap
        from PySide6.QtGui import QBitmap, QRegion
        mask = img.createAlphaMask()
        rect = QRegion(QBitmap.fromImage(mask)).boundingRect()
        if rect.isValid() and rect.width() >= 8 and rect.height() >= 8:
            if rect != img.rect():
                return pixmap.copy(rect)
    except Exception:
        pass
    return pixmap


def device_pixel_ratio():
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        screen = app.primaryScreen() if app else None
        if screen is not None:
            ratio = float(screen.devicePixelRatio())
            if ratio > 0:
                return ratio
    except Exception:
        pass
    return 1.0


def hidpi_pixmap(pixmap, width, height, *, smooth=True, keep_aspect=True):
    '''按屏幕 DPI 渲染图标，避免高分屏下被放大发虚。'''

    if pixmap is None or pixmap.isNull():
        return pixmap
    dpr = device_pixel_ratio()
    mode = Qt.KeepAspectRatio if keep_aspect else Qt.IgnoreAspectRatio
    transform = Qt.SmoothTransformation if smooth else Qt.FastTransformation
    scaled = pixmap.scaled(
        max(1, int(round(width * dpr))),
        max(1, int(round(height * dpr))),
        mode, transform,
    )
    scaled.setDevicePixelRatio(dpr)
    return scaled


def rounded_pixmap(pixmap, radius=10):
    if pixmap is None or pixmap.isNull():
        return pixmap
    dpr = pixmap.devicePixelRatio() or 1.0
    size = pixmap.size()
    out = QPixmap(size)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    phys_radius = radius * dpr
    path.addRoundedRect(0, 0, size.width(), size.height(), phys_radius, phys_radius)
    painter.setClipPath(path)
    # 画布是物理像素：按源图的物理尺寸 1:1 绘制，避免带 DPR 的图缩到左上角
    painter.drawPixmap(
        QRectF(0, 0, size.width(), size.height()),
        pixmap,
        QRectF(0, 0, pixmap.width(), pixmap.height()),
    )
    painter.end()
    out.setDevicePixelRatio(dpr)
    return out


def initial_pixmap(text, color, size=44, radius=10):
    dpr = device_pixel_ratio()
    phys = max(1, int(round(size * dpr)))
    pixmap = QPixmap(phys, phys)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(0, 0, phys, phys, radius * dpr, radius * dpr)
    painter.setPen(QColor('#ffffff'))
    painter.setFont(ui_font(max(12, int(size * 0.34)), QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, str(text or ''))
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class MemoryUsageBar(QWidget):
    """PCL 风格内存条：左段为已使用内存，紧随其后的为游戏分配。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._used = 0.0
        self._alloc = 0.0
        self.setFixedHeight(10)
        self.setMinimumWidth(120)

    def set_values(self, used_ratio, alloc_ratio):
        try:
            self._used = max(0.0, min(1.0, float(used_ratio)))
        except (TypeError, ValueError):
            self._used = 0.0
        try:
            self._alloc = max(0.0, min(1.0 - self._used, float(alloc_ratio)))
        except (TypeError, ValueError):
            self._alloc = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = 5.0
        painter.setPen(Qt.NoPen)
        palette = QApplication.palette()
        light = palette.color(QPalette.Window).lightness() > 128
        painter.setBrush(QColor(0, 0, 0, 26) if light else QColor(255, 255, 255, 26))
        painter.drawRoundedRect(rect, radius, radius)
        width = rect.width()
        used_w = width * self._used
        alloc_w = width * self._alloc
        if used_w > 0.5:
            painter.setBrush(QColor('#3f8ae0'))
            painter.drawRoundedRect(
                QRectF(0, 0, max(used_w, radius * 2), rect.height()), radius, radius
            )
        if alloc_w > 0.5:
            painter.setBrush(QColor('#f0a030'))
            start = max(0.0, used_w - 2.0)
            painter.drawRoundedRect(
                QRectF(start, 0, max(alloc_w, radius * 2), rect.height()), radius, radius
            )
        painter.end()
