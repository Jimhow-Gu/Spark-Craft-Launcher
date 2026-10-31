'''启动器品牌图标：根据深浅主题调整白色 logo 的对比度。'''

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel


class BrandLogo(QLabel):
    def __init__(self, size: int = 28, parent=None):
        super().__init__(parent)
        self._size = int(size)
        self._light = False
        self._source = QPixmap()
        self.setFixedSize(self._size, self._size)
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet('background:transparent;')
        self._load()
        self._refresh()

    def _load(self):
        base = Path(__file__).resolve().parent.parent
        path = base / 'imgs' / 'sparkler_alpha.png'
        if not path.is_file():
            path = base / 'imgs' / 'sparkler.png'
        if not path.is_file():
            return
        try:
            from ui.widgets.settings_widgets import hidpi_pixmap, trim_transparent
            pixmap = trim_transparent(QPixmap(str(path)))
            if pixmap.isNull():
                return
            self._source = hidpi_pixmap(
                pixmap, self._size, self._size,
                smooth=True, keep_aspect=True,
            )
        except Exception:
            self._source = QPixmap()

    def set_light(self, light: bool):
        self._light = bool(light)
        self._refresh()

    def _refresh(self):
        if self._source.isNull():
            self.clear()
            return
        if not self._light:
            self.setPixmap(self._source)
            return
        image = self._source.toImage().convertToFormat(QImage.Format_ARGB32)
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() <= 0:
                    continue
                high = max(color.red(), color.green(), color.blue())
                low = min(color.red(), color.green(), color.blue())
                if high - low <= 35 and high >= 120:
                    # 只压暗颜色，保留原始透明度，避免浅色模式变成实心色块。
                    color.setRgb(52, 59, 72, color.alpha())
                elif high >= 190:
                    color.setRgb(
                        int(color.red() * 0.68),
                        int(color.green() * 0.68),
                        int(color.blue() * 0.68),
                        color.alpha(),
                    )
                image.setPixelColor(x, y, color)
        self.setPixmap(QPixmap.fromImage(image))


__all__ = ['BrandLogo']
