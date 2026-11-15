'''共享 MC 头像控件：侧栏账户卡与账户页复用同一实现。'''

from pathlib import Path
from PySide6.QtCore import Qt, Signal, QPoint, QRectF, QUrl
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QPainterPath, QImage, QPen, QPalette
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QLabel
from core.app_font import ui_font
from core.config import ConfigManager
from ui.design import IMAGE_PLACEHOLDER_DARK, IMAGE_PLACEHOLDER_LIGHT
from ui.widgets.mc_head import render_head

_DEFAULT_HEAD_PATH = None


def default_head_path():
    '''未登录时随机使用 Steve/Alex 头颅，同一次运行内保持稳定。'''

    global _DEFAULT_HEAD_PATH
    if _DEFAULT_HEAD_PATH is not None and _DEFAULT_HEAD_PATH.is_file():
        return _DEFAULT_HEAD_PATH
    import random
    base = Path(__file__).resolve().parent.parent / 'imgs'
    name = random.choice(('steve_head.png', 'alex_head.png'))
    path = base / name
    _DEFAULT_HEAD_PATH = path if path.is_file() else base / 'steve_head.png'
    return _DEFAULT_HEAD_PATH


class AvatarLabel(QLabel):
    all_failed = Signal()
    clicked = Signal()
    context_menu_requested = Signal(QPoint)
    source_changed = Signal()
    def __init__(self, parent=None, size=96, radius=12):
        super().__init__(parent)
        self._size = int(size)
        self._radius = int(radius)
        self._light = self.palette().color(QPalette.Window).lightness() > 128
        self._showing_default = False
        self._setting_default = False
        self._showing_person = False
        self._setting_person = False
        self._showing_head = False
        self._setting_head = False
        self._head_source = None
        self._head_normal = None
        self._head_hover = None
        self._hovered = False
        self._skin_interactive = False
        self.setFixedSize(self._size, self._size)
        self.setAlignment(Qt.AlignCenter)
        self._update_placeholder_style()
        self._name = '?'
        self._manager = QNetworkAccessManager(self)
        self._manager.finished.connect(self._on_image_loaded)
        self._replies = set()
        self._image_urls = []
        self._fallback_head = ''
        self._cache_path = None
        self._set_default()

    def _placeholder_color(self):
        return IMAGE_PLACEHOLDER_LIGHT if self._light else IMAGE_PLACEHOLDER_DARK

    def _update_placeholder_style(self):
        if self._showing_person:
            self.setStyleSheet('border-radius: 0px; background: transparent;')
            return
        if self._showing_head:
            # 头像本身已经有阴影，不再额外绘制淡色圆角边框。
            self.setStyleSheet('border-radius: 0px; background: transparent;')
            return
        self.setStyleSheet(
            f'border-radius: {self._radius}px; background: {self._placeholder_color()};'
        )

    def set_light(self, light):
        light = bool(light)
        changed = light != self._light
        self._light = light
        self._update_placeholder_style()
        if self._showing_person:
            self._showing_person = False
            self.set_person_icon()
        elif self._showing_head:
            source = self._head_source
            if source is not None:
                self._set_3d_head(source)
            else:
                self._update_placeholder_style()
        elif self._showing_default and changed:
            self._set_default()

    def setPixmap(self, pixmap):
        if self._showing_person and not self._setting_person:
            self._showing_person = False
            self._update_placeholder_style()
        if self._showing_head and not self._setting_head:
            self._showing_head = False
            self._head_source = None
            self._head_normal = None
            self._head_hover = None
            self._hovered = False
            self.setToolTip('')
            self.setCursor(Qt.ArrowCursor)
            self._update_placeholder_style()
        if not self._setting_default:
            self._showing_default = False
        super().setPixmap(pixmap)

    def set_skin_interactive(self, enabled: bool):
        self._skin_interactive = bool(enabled)
        if self._showing_head and self._skin_interactive:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip('点击更换皮肤（右键查看更多选项）')
        elif not self._showing_head:
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip('')

    def set_head_source(self, pixmap):
        """用与账户页完全相同的皮肤源渲染，保证侧栏和账户卡头像一致。"""
        self._set_3d_head(pixmap)

    def head_source(self):
        if self._showing_head and self._head_source is not None:
            return self._head_source
        return None

    def _apply_head_pixmap(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        self._showing_head = True
        self._showing_person = False
        self._showing_default = False
        self._update_placeholder_style()
        self._setting_head = True
        try:
            self.setPixmap(pixmap)
        finally:
            self._setting_head = False

    def _set_3d_head(self, pixmap):
        if pixmap is None or pixmap.isNull():
            self._set_default()
            return
        from ui.widgets.settings_widgets import device_pixel_ratio
        dpr = device_pixel_ratio()
        normal = render_head(
            pixmap, self._size, dpr, light=self._light, strong_shadow=False
        )
        hover = render_head(
            pixmap, self._size, dpr, light=self._light, strong_shadow=True
        )
        if normal.isNull():
            self._set_default()
            return
        self._head_source = pixmap
        self._head_normal = normal
        self._head_hover = hover
        self._hovered = bool(self._skin_interactive and self.underMouse())
        self._apply_head_pixmap(hover if self._hovered else normal)
        if self._skin_interactive:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip('点击更换皮肤（右键查看更多选项）')
        else:
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip('')
        self.source_changed.emit()

    def enterEvent(self, event):
        self._hovered = True
        if (
            self._skin_interactive and self._showing_head
            and self._head_hover is not None
        ):
            self._apply_head_pixmap(self._head_hover)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if self._showing_head and self._head_normal is not None:
            self._apply_head_pixmap(self._head_normal)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._skin_interactive and self._showing_head
            and event.button() == Qt.LeftButton
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        if self._skin_interactive and self._showing_head:
            self.context_menu_requested.emit(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def set_person_icon(self):
        '''未登录正版账号时使用通用人物图标代替头像。'''

        if self._showing_person and not self._setting_person:
            return
        self._showing_person = True
        self._showing_head = False
        self._showing_default = False
        self._head_source = None
        self._head_normal = None
        self._head_hover = None
        self._hovered = False
        self.setToolTip('')
        self.setCursor(Qt.ArrowCursor)
        self._image_urls = []
        self._fallback_head = ''
        self._cache_path = None
        for reply in list(self._replies):
            try:
                reply.abort()
            except RuntimeError:
                pass
        self._replies.clear()
        self._update_placeholder_style()
        from ui.widgets.settings_widgets import device_pixel_ratio
        dpr = device_pixel_ratio()
        phys = max(1, int(round(self._size * dpr)))
        pixmap = QPixmap(phys, phys)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor('#4F8F7F') if self._light else QColor('#8FD8C0')
        pen = QPen(color, max(2.0, phys * 0.045))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        head_r = phys * 0.16
        painter.drawEllipse(
            QRectF(phys * 0.5 - head_r, phys * 0.35 - head_r, head_r * 2, head_r * 2)
        )
        painter.drawArc(
            QRectF(phys * 0.24, phys * 0.55, phys * 0.52, phys * 0.31),
            0, 180 * 16,
        )
        painter.end()
        pixmap.setDevicePixelRatio(dpr)
        self._setting_person = True
        try:
            self.setPixmap(pixmap)
        finally:
            self._setting_person = False
        self.source_changed.emit()

    def is_person_placeholder(self):
        return bool(self._showing_person)

    @staticmethod
    def config():
        return ConfigManager()

    def _set_default(self, text=None):
        if text is not None:
            self._name = text
        from ui.widgets.settings_widgets import device_pixel_ratio
        dpr = device_pixel_ratio()
        phys = max(1, int(round(self._size * dpr)))
        pixmap = QPixmap(phys, phys)
        pixmap.fill(QColor(self._placeholder_color()))
        painter = QPainter(pixmap)
        painter.setPen(QColor('#222222') if self._light else QColor('#e3fcfc'))
        painter.setFont(ui_font(max(12, int(self._size * 0.36)), QFont.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, self._name[0].upper() if self._name else '?')
        painter.end()
        pixmap.setDevicePixelRatio(dpr)
        self._setting_default = True
        self.setPixmap(self._rounded(pixmap, self._radius))
        self._setting_default = False
        self._showing_default = True

    @staticmethod
    def _rounded(pixmap, radius=12):
        if pixmap.isNull():
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
        from PySide6.QtCore import QRectF
        painter.drawPixmap(
            QRectF(0, 0, size.width(), size.height()),
            pixmap,
            QRectF(0, 0, pixmap.width(), pixmap.height()),
        )
        # 用一次性绘制的静态暗边代替实时 QGraphicsDropShadowEffect。
        # 实时阴影会随视频背景每帧重绘，实测会把启动器空闲 CPU 拉高很多。
        painter.setClipping(False)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 58), max(1.0, 1.2 * dpr)))
        painter.drawPath(path)
        painter.end()
        out.setDevicePixelRatio(dpr)
        return out

    @staticmethod
    def _is_skin_image(pixmap):
        '''只有标准皮肤比例才按头部分区裁剪；96x96 等头像图不能当皮肤切。'''
        if pixmap is None or pixmap.isNull():
            return False
        width, height = pixmap.width(), pixmap.height()
        if width < 64 or width % 64 != 0:
            return False
        return height in (32, 64) or (height >= 64 and height % 64 == 0)

    @staticmethod
    def _head_from_skin(pixmap, size=96):
        if AvatarLabel._is_skin_image(pixmap):
            # HD 皮肤（128/256）的头部坐标要按 64 基准等比放大
            scale = max(1, int(round(pixmap.width() / 64.0)))
            out = QImage(8, 8, QImage.Format_ARGB32_Premultiplied)
            out.fill(Qt.transparent)
            painter = QPainter(out)
            face = pixmap.copy(8 * scale, 8 * scale, 8 * scale, 8 * scale).scaled(
                8, 8, Qt.IgnoreAspectRatio, Qt.FastTransformation
            )
            painter.drawPixmap(0, 0, face)
            if pixmap.height() >= 16 * scale:
                hat = pixmap.copy(40 * scale, 8 * scale, 8 * scale, 8 * scale).scaled(
                    8, 8, Qt.IgnoreAspectRatio, Qt.FastTransformation
                )
                painter.drawPixmap(0, 0, hat)
            painter.end()
            pixmap = QPixmap.fromImage(out)
        from ui.widgets.settings_widgets import hidpi_pixmap
        # Minecraft 头像是像素画：用最近邻缩放保持清晰，平滑缩放会糊成色块
        return hidpi_pixmap(pixmap, size, size, smooth=False)

    def setText(self, text):
        self._name = text or '?'
        self._set_default()

    def set_image_url(self, urls, fallback_head=None, cache_path=None):
        if isinstance(urls, str):
            urls = [urls]
        from ui.widgets.settings_widgets import hidpi_pixmap
        self._cache_path = Path(cache_path) if cache_path else None
        # 有缓存时直接使用；但旧版本可能把头图写进皮肤缓存，必须先按
        # 64 基准尺寸校验，否则 96x96 头图会被当成皮肤永久平铺。
        if self._cache_path is not None and self._cache_path.is_file():
            cached = QPixmap(str(self._cache_path))
            if not cached.isNull() and self._is_skin_image(cached):
                self._image_urls = []
                self._fallback_head = ''
                self._set_3d_head(cached)
                return
            try:
                self._cache_path.unlink()
            except OSError:
                pass
        self._image_urls = [u for u in urls or [] if u]
        if fallback_head is None:
            fallback_head = Path(__file__).resolve().parent.parent / 'imgs' / 'steve_head.png'
        self._fallback_head = str(fallback_head) if fallback_head and Path(fallback_head).is_file() else ''
        # 网络头像加载期间先显示同一个默认头颅，而不是字母占位；
        # 侧栏会复用这份 source，加载完成后再一起切换。
        if self._fallback_head:
            self.set_head_file(self._fallback_head)
        else:
            self._set_default()
        self._try_next_image()

    def _try_next_image(self):
        if self._showing_person:
            return
        if not self._image_urls:
            fallback = self._fallback_head
            self._fallback_head = ''
            if fallback:
                self.set_head_file(fallback)
            try:
                self.all_failed.emit()
            except RuntimeError:
                pass
            return
        url = self._image_urls.pop(0)
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b'User-Agent', b'PMGL-Launcher/1.1')
        try:
            req.setTransferTimeout(5000)
        except Exception:
            pass
        reply = self._manager.get(req)
        if not bool(self.config().get('verify_ssl', True)):
            reply.sslErrors.connect(
                lambda _errors, r=reply: r.ignoreSslErrors()
            )
        self._replies.add(reply)

    def _on_image_loaded(self, reply):
        self._replies.discard(reply)
        if self._showing_person:
            reply.deleteLater()
            return
        loaded = False
        if reply.error() == QNetworkReply.NoError:
            pix = QPixmap()
            raw = reply.readAll()
            cache_path = getattr(self, '_cache_path', None)
            if pix.loadFromData(raw) and not pix.isNull():
                is_skin = self._is_skin_image(pix)
                self._set_3d_head(pix)
                if cache_path is not None and raw:
                    try:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        if is_skin:
                            # 只有完整皮肤才进入 3D 皮肤缓存。
                            cache_path.write_bytes(bytes(raw))
                        else:
                            # 96x96 等头图另存，绝不能污染 microsoft_avatar_*.png。
                            head_cache = cache_path.with_name(
                                cache_path.stem + '_head.png'
                            )
                            head_cache.write_bytes(bytes(raw))
                    except Exception:
                        pass
                loaded = True
        reply.deleteLater()
        if not loaded:
            self._try_next_image()

    def set_skin_file(self, path):
        pix = QPixmap(str(path)) if path else QPixmap()
        if pix.isNull():
            self._set_default()
            return
        self._set_3d_head(pix)

    def set_head_file(self, path):
        '''显示头像文件；默认 Steve/Alex 头像优先用配套完整皮肤渲染 3D。'''

        path_obj = Path(str(path)) if path else Path()
        pix = QPixmap(str(path_obj)) if path else QPixmap()
        if pix.isNull():
            self._set_default()
            return
        name = path_obj.name.lower()
        if name in ('steve_head.png', 'alex_head.png'):
            skin = path_obj.with_name(name.replace('_head.png', '_skin.png'))
            if skin.is_file():
                skin_pix = QPixmap(str(skin))
                if not skin_pix.isNull():
                    pix = skin_pix
        self._set_3d_head(pix)
