'''按 PCL 的方式渲染 MC 头颅：底层脸 + 更大的帽子层，形成蓬松的立体边缘。'''

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap


def is_skin_image(pixmap: QPixmap) -> bool:
    if pixmap is None or pixmap.isNull():
        return False
    width, height = pixmap.width(), pixmap.height()
    if width < 64 or width % 64 != 0:
        return False
    return height in (32, 64) or (height >= 64 and height % 64 == 0)


def _face(pixmap: QPixmap, x: int, y: int, scale: int) -> QPixmap:
    return pixmap.copy(
        x * scale, y * scale, 8 * scale, 8 * scale
    ).scaled(8, 8, Qt.IgnoreAspectRatio, Qt.FastTransformation)


def _has_pixels(pixmap: QPixmap) -> bool:
    image = pixmap.toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 8:
                return True
    return False


def _hat_overlay(pixmap: QPixmap, scale: int) -> QPixmap:
    """返回有实际细节的帽子层；全透明或与脸完全相同的覆盖层不参与渲染。"""

    base = _face(pixmap, 8, 8, scale)
    hat = _face(pixmap, 40, 8, scale)
    if not _has_pixels(hat):
        return None
    base_image = base.toImage()
    hat_image = hat.toImage()
    transparent = 0
    differs = 0
    for y in range(8):
        for x in range(8):
            hat_px = hat_image.pixelColor(x, y)
            if hat_px.alpha() <= 8:
                transparent += 1
                continue
            if hat_px.rgba() != base_image.pixelColor(x, y).rgba():
                differs += 1
    if not transparent and not differs:
        return None
    return hat


def _shadow(source: QPixmap, size: int, pad: int, light: bool,
            strong: bool=False) -> QImage:
    '''把 alpha 缩小再放大，生成一次性的柔和阴影。'''

    canvas = QImage(size + pad * 2, size + pad * 2,
                    QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap(pad, pad, source)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    if light:
        alpha = 64 if strong else 36
    else:
        alpha = 108 if strong else 60
    painter.fillRect(canvas.rect(), QColor(0, 0, 0, alpha) if light
                     else QColor(186, 208, 208, alpha))
    painter.end()
    small = max(2, (size + pad * 2) // 5)
    return canvas.scaled(
        small, small, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    ).scaled(
        size + pad * 2, size + pad * 2,
        Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )


def render_head(pixmap: QPixmap, logical_size: int, dpr: float = 1.0,
                light: bool = False, strong_shadow: bool = False) -> QPixmap:
    '''PCL 风格：脸占 75%，帽子覆盖层占 87.5%，帽子层在外圈形成立体边缘。'''

    if pixmap is None or pixmap.isNull():
        return QPixmap()
    dpr = max(1.0, float(dpr or 1.0))
    physical = max(8, int(round(int(logical_size) * dpr)))
    image = QImage(physical, physical, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, False)

    base = None
    overlay = None
    if is_skin_image(pixmap):
        scale = max(1, int(round(pixmap.width() / 64.0)))
        base = _face(pixmap, 8, 8, scale)
        if pixmap.height() >= 16 * scale:
            overlay = _hat_overlay(pixmap, scale)
    else:
        overlay = pixmap

    # PCL 的 ImgBack=48/64，ImgFore=56/64；没有帽子层时用大头占满。
    if overlay is not None and base is not None:
        base_ratio, over_ratio = 0.75, 0.875
    elif overlay is not None:
        base_ratio, over_ratio = 0.0, 0.875
    else:
        # PCL 的 ImgBack 固定为 48/64；没有帽子层时保留完整脸和边距，
        # 不再放大到 87.5%，否则会变成贴边的平面大方块。
        base_ratio, over_ratio = 0.75, 0.0

    def scaled(pix, ratio):
        px = max(4, int(round(physical * ratio)))
        return pix.scaled(
            px, px, Qt.IgnoreAspectRatio, Qt.FastTransformation
        ), px

    base_pix = over_pix = None
    base_px = over_px = 0
    if base is not None and base_ratio:
        base_pix, base_px = scaled(base, base_ratio)
    if overlay is not None and over_ratio:
        over_pix, over_px = scaled(overlay, over_ratio)

    shadow_source = over_pix or base_pix
    if shadow_source is not None:
        shadow_size = over_px if over_pix is not None else base_px
        max_pad = max(1, (physical - shadow_size) // 2 - 1)
        desired = 0.13 if strong_shadow else 0.10
        pad = min(max_pad, max(1, int(round(shadow_size * desired))))
        soft = _shadow(shadow_source, shadow_size, pad, light, strong_shadow)
        painter.drawImage(
            QRectF(
                (physical - soft.width()) / 2.0,
                (physical - soft.height()) / 2.0 + physical * 0.012,
                soft.width(), soft.height(),
            ),
            soft,
            QRectF(0, 0, soft.width(), soft.height()),
        )

    if base_pix is not None:
        painter.drawPixmap(
            QRectF((physical - base_px) / 2.0,
                   (physical - base_px) / 2.0, base_px, base_px),
            base_pix, QRectF(0, 0, base_pix.width(), base_pix.height()),
        )
    if over_pix is not None:
        painter.drawPixmap(
            QRectF((physical - over_px) / 2.0,
                   (physical - over_px) / 2.0, over_px, over_px),
            over_pix, QRectF(0, 0, over_pix.width(), over_pix.height()),
        )
    painter.end()

    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(dpr)
    return result


__all__ = ['is_skin_image', 'render_head']
