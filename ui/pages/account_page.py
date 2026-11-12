'''
账号页面 - 离线登录 / 正版账号登录
适配新版auth.py，彻底移除所有device_code相关代码
'''

import shutil
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QScrollArea, QMessageBox, QComboBox, QFileDialog, QApplication, QMenu, QDialog, QListWidget, QListWidgetItem, QCheckBox, QToolButton, QListView, QSizePolicy, QStylePainter, QStyleOptionComboBox, QStyle, QButtonGroup, QGraphicsOpacityEffect, QGridLayout)
from ui.widgets.themed_checkbox import ThemedCheckBox
from ui.widgets.login_icons import login_icon
from ui.widgets.mc_head import render_head
from PySide6.QtCore import (Qt, Signal, QUrl, QThread, QTimer, QSize, QObject, QEasingCurve, QEvent, QPropertyAnimation, QRectF, QPoint, Property)
from PySide6.QtGui import (QFont, QPixmap, QPainter, QColor, QPainterPath, QImage, QAction, QIcon, QPalette, QPen)
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkReply, QNetworkRequest)
from core.app_font import ui_font
from core.config import ConfigManager
from core.secret import unprotect_secret
from core.paths import scl_dir
from ui.widgets.frameless_dialog import (FramelessDialog, FramelessInputDialog, FramelessListDialog)
from ui.widgets.height_animation import HeightAnimation
from ui.widgets.rotating_chevron import RotatingChevronButton
from ui.widgets.rounded_surface import RoundedGroupBox, RoundedFrame
from ui.widgets.toast import notify_network_failure, show_toast
from ui.design import IMAGE_PLACEHOLDER_DARK, IMAGE_PLACEHOLDER_LIGHT
from ui.widgets.painted_icons import painted_icon, set_button_icon
from core.auth import (AuthManager, MicrosoftSilentLoginWorker, MicrosoftProfileWorker, SavedMicrosoftLoginWorker, MicrosoftDeviceLoginWorker, ThirdPartyLoginWorker, CapeListWorker, CapeApplyWorker, DEFAULT_LITTLESKIN_API, DEFAULT_LITTLESKIN_REGISTER, friendly_auth_error)
MICROSOFT_PURCHASE_URL = 'https://www.minecraft.net/zh-hans/store/minecraft-java-bedrock-edition-pc'
MICROSOFT_OFFICIAL_URL = 'https://www.minecraft.net/zh-hans'

def _show_microsoft_login_notice(parent, message: str, kind: str='general'):
    '''正版登录失败提示。'''

    from ui.widgets.dark_dialog import DarkQuestionDialog
    if kind == 'no_game':
        dialog = DarkQuestionDialog(parent, '未拥有游戏', '该正版账号未拥有 Minecraft。你可以前往购买，或确认账号后再试。', actions=[('前往购买', 'buy', True), ('确定', 'ok', False)])
        dialog.exec()
        if dialog.choice == 'buy':
            webbrowser.open(MICROSOFT_PURCHASE_URL)
        return
    if kind == 'network':
        notify_network_failure(parent, '验证登录', '当前网络问题导致无法登录，请使用加速器/VPN后重试！')
        return
    text = friendly_auth_error(message, kind)
    dialog = DarkQuestionDialog(parent, '登录失败', text, actions=[('确定', 'ok', True)])
    dialog.exec()


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


def _stop_combo_list_animation(combo):
    anim = getattr(combo, '_list_anim', None)
    combo._list_anim = None
    combo._list_effect = None
    if anim is not None:
        try:
            anim.stop()
            anim.deleteLater()
        except RuntimeError:
            pass
    view = combo.view()
    if view is not None:
        try:
            view.setGraphicsEffect(None)
        except RuntimeError:
            pass


def _animate_combo_list(combo):
    view = combo.view()
    if view is None:
        return
    _stop_combo_list_animation(combo)
    effect = QGraphicsOpacityEffect(view)
    view.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b'opacity', combo)
    animation.setDuration(140)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    combo._list_anim = animation
    combo._list_effect = effect

    def finish():
        if getattr(combo, '_list_anim', None) is animation:
            combo._list_anim = None
            combo._list_effect = None
            try:
                view.setGraphicsEffect(None)
            except RuntimeError:
                pass
        try:
            animation.deleteLater()
        except RuntimeError:
            pass

    animation.finished.connect(finish)
    animation.start()


class AvatarComboBox(QComboBox):
    '''关闭状态下手动绘制当前头像，避免 QComboBox 按文字行高裁掉头像下半部分。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self._arrow_angle = 0.0
        self._arrow_anim = None

    def _get_arrow_angle(self):
        return self._arrow_angle

    def _set_arrow_angle(self, value):
        self._arrow_angle = float(value)
        self.update()

    arrowAngle = Property(float, _get_arrow_angle, _set_arrow_angle)

    def _animate_arrow(self, expanded):
        old = self._arrow_anim
        if old is not None:
            try:
                old.stop()
                old.deleteLater()
            except RuntimeError:
                pass
        animation = QPropertyAnimation(self, b'arrowAngle', self)
        animation.setDuration(150)
        animation.setStartValue(self._arrow_angle)
        animation.setEndValue(180.0 if expanded else 0.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(animation.deleteLater)
        self._arrow_anim = animation
        animation.start()

    def showPopup(self):
        # 弹出列表跟随组合框宽度，避免条目宽度把列表撑出账户卡片区域。
        view = self.view()
        width = max(210, self.width())
        view.setMinimumWidth(width)
        view.setMaximumWidth(width)
        self._animate_arrow(True)
        super().showPopup()
        QTimer.singleShot(0, lambda c=self: _animate_combo_list(c))

    def hidePopup(self):
        self._animate_arrow(False)
        _stop_combo_list_animation(self)
        super().hidePopup()

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentIcon = QIcon()
        option.currentText = ''
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        edit_rect = self.style().subControlRect(
            QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self
        )
        icon = self.itemIcon(self.currentIndex())
        text_rect = edit_rect
        if not icon.isNull():
            size = self.iconSize()
            dpr = max(1.0, float(self.devicePixelRatioF()))
            physical = QSize(
                max(1, int(round(size.width() * dpr))),
                max(1, int(round(size.height() * dpr))),
            )
            pix = icon.pixmap(physical)
            if not pix.isNull():
                pix.setDevicePixelRatio(dpr)
                logical_w = pix.width() / dpr
                logical_h = pix.height() / dpr
                x = edit_rect.left()
                y = int(round(edit_rect.center().y() - logical_h / 2.0 + 1))
                painter.drawPixmap(x, y, pix)
                text_rect = edit_rect.adjusted(int(round(logical_w)) + 10, 0, 0, 0)
        painter.setPen(self.palette().color(QPalette.ButtonText))
        painter.setFont(self.font())
        elided = self.fontMetrics().elidedText(
            self.currentText(), Qt.ElideRight, max(0, text_rect.width())
        )
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)
        arrow_rect = self.style().subControlRect(
            QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, self
        )
        if arrow_rect.isValid():
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(self.palette().color(QPalette.ButtonText), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            center = arrow_rect.center()
            painter.translate(center.x(), center.y())
            painter.rotate(self._arrow_angle)
            painter.drawLine(-5, -2, 0, 3)
            painter.drawLine(0, 3, 5, -2)
            painter.restore()


class AutoSelectLineEdit(QLineEdit):
    '''首次点击进入输入框时全选；再次点击恢复普通光标定位。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self._select_all_pending = False

    def _finish_focus_select(self):
        if self._select_all_pending:
            self._select_all_pending = False
            self.selectAll()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._select_all_pending = True
        QTimer.singleShot(0, self._finish_focus_select)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self._select_all_pending:
            self._select_all_pending = False
            QTimer.singleShot(0, self.selectAll)


class HistoryComboBox(QComboBox):
    '''可编辑历史列表：右侧绘制箭头，展开时旋转。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self._arrow_angle = 0.0
        self._arrow_anim = None

    def _get_arrow_angle(self):
        return self._arrow_angle

    def _set_arrow_angle(self, value):
        self._arrow_angle = float(value)
        self.update()

    arrowAngle = Property(float, _get_arrow_angle, _set_arrow_angle)

    def _animate_arrow(self, expanded):
        old = self._arrow_anim
        if old is not None:
            try:
                old.stop()
                old.deleteLater()
            except RuntimeError:
                pass
        animation = QPropertyAnimation(self, b'arrowAngle', self)
        animation.setDuration(150)
        animation.setStartValue(self._arrow_angle)
        animation.setEndValue(180.0 if expanded else 0.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(animation.deleteLater)
        self._arrow_anim = animation
        animation.start()

    def showPopup(self):
        self._animate_arrow(True)
        super().showPopup()
        QTimer.singleShot(0, lambda c=self: _animate_combo_list(c))

    def hidePopup(self):
        self._animate_arrow(False)
        _stop_combo_list_animation(self)
        super().hidePopup()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() <= 0 and not self.isEditable():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor('#8F96A3')
        try:
            if self.palette().color(QPalette.Window).lightness() > 128:
                color = QColor('#4F6F6F')
        except Exception:
            pass
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        cx = self.width() - 16
        cy = self.height() // 2 + 1
        painter.translate(cx, cy)
        painter.rotate(self._arrow_angle)
        painter.drawLine(-5, -2, 0, 3)
        painter.drawLine(0, 3, 5, -2)
        painter.end()


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
            # 所有尺寸共用同一层半透明底，侧栏和账户卡不会再一个带边框、
            # 一个像直接贴在卡片上。
            tint = '255,255,255' if not self._light else '18,24,24'
            self.setStyleSheet(
                f'border-radius: {self._radius}px;'
                f' background: rgba({tint},0.045);'
            )
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
        # 有缓存时直接使用，不再等待网络（断网/机房封锁环境秒开）
        if self._cache_path is not None and self._cache_path.is_file():
            cached = QPixmap(str(self._cache_path))
            if not cached.isNull():
                self._image_urls = []
                self._fallback_head = ''
                self._set_3d_head(cached)
                return
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
        if not bool(self.config().get('verify_ssl', False)):
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
                url = reply.url().toString()
                is_official = 'textures.minecraft.net' in url and pix.width() >= 64
                self._set_3d_head(pix)
                if cache_path is not None and raw:
                    try:
                        # 缓存完整皮肤/原始头像，下一次仍可渲染 3D 头颅。
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_bytes(bytes(raw))
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


class SavedAccountIconLoader(QObject):
    '''异步加载已保存正版账号的头像（官方皮肤纹理优先）。'''

    ready = Signal(str, QPixmap)
    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._manager.finished.connect(self._on_finished)
        self._pending = {}

    def request(self, key: str, urls):
        self._next(key, [u for u in urls or [] if u])

    def _next(self, key: str, urls):
        if not urls:
            return
        url = urls.pop(0)
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b'User-Agent', b'PMGL-Launcher/1.1')
        try:
            req.setTransferTimeout(5000)
        except Exception:
            pass
        reply = self._manager.get(req)
        if not bool(ConfigManager().get('verify_ssl', False)):
            reply.sslErrors.connect(
                lambda _errors, r=reply: r.ignoreSslErrors()
            )
        self._pending[reply] = key, urls, url

    def _on_finished(self, reply):
        key, urls, url = self._pending.pop(reply, ('', [], ''))
        loaded = None
        if key and reply.error() == QNetworkReply.NoError:
            image = QImage()
            if image.loadFromData(reply.readAll()) and not image.isNull():
                pix = QPixmap.fromImage(image)
                if 'textures.minecraft.net' in url:
                    pix = AvatarLabel._head_from_skin(pix, 42)
                else:
                    pix = pix.scaled(42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                loaded = pix
        reply.deleteLater()
        if loaded is not None:
            self.ready.emit(key, loaded)
            return
        if key:
            self._next(key, urls)


class SkinPreviewWorker(QThread):
    done = Signal(str)
    def __init__(self, settings):
        super().__init__()
        self.settings = dict(settings)

    def run(self):
        try:
            from core.skin import SkinManager
            path = SkinManager().preview_skin(self.settings)
            self.done.emit(str(path) if path else '')
        except Exception:
            self.done.emit('')


class SkinUploadWorker(QThread):
    success = Signal()
    error = Signal(str)
    def __init__(self, path, model):
        super().__init__()
        self.path = path
        self.model = model

    def run(self):
        try:
            AuthManager().upload_microsoft_skin(self.path, self.model)
            self.success.emit()
        except Exception as exc:
            self.error.emit(friendly_auth_error(exc))
            return


class MicrosoftProfileSkinWorker(QThread):
    '''后台刷新正版账号官方资料，并把官方皮肤缓存到 SCL/skins。'''

    success = Signal(dict)
    error = Signal(str, str)
    def __init__(self):
        super().__init__()

    def run(self):
        try:
            from core.net import HttpClient
            from core.skin import microsoft_skin_cache_path
            auth = AuthManager()
            try:
                profile = auth.get_minecraft_profile()
            except Exception as original_error:
                account = auth.config.get('microsoft_account') or {}
                try:
                    profile = auth.get_public_minecraft_profile(
                        account.get('uuid', ''), account.get('name', '')
                    )
                except Exception:
                    raise original_error
            skin = profile.get('active_skin') or {}
            skin_path = ''
            name = str(profile.get('name') or '').strip()
            profile_id = str(profile.get('id') or '').strip()
            account = auth.config.get('microsoft_account') or {}
            uuid = str(profile_id or account.get('uuid') or '').replace('-', '').strip()
            dest = microsoft_skin_cache_path(uuid, name)
            candidates = []
            if skin.get('url'):
                candidates.append(str(skin['url']))
            # 特殊网络环境下官方纹理域名可能不可达，回退到多个第三方皮肤镜像。
            if uuid:
                candidates.extend([
                    f'https://minotar.net/skin/{uuid}',
                    f'https://mc-heads.net/skin/{uuid}',
                    f'https://crafatar.com/skins/{uuid}',
                    f'https://mineskin.eu/skin/{uuid}',
                ])
            if name:
                candidates.extend([
                    f'https://minotar.net/skin/{name}',
                    f'https://mc-heads.net/skin/{name}',
                    f'https://mineskin.eu/skin/{name}',
                ])
            http = HttpClient(timeout=15, retries=1)
            for url in candidates:
                try:
                    http.download(url, dest, allow_slow_fallback=False)
                except Exception:
                    continue
                if dest.is_file() and dest.stat().st_size > 64:
                    skin_path = str(dest)
                    break
            self.success.emit({'profile': profile, 'skin': skin, 'skin_path': skin_path})
        except Exception as exc:
            kind = str(getattr(exc, 'kind', '') or 'general')
            self.error.emit(friendly_auth_error(exc, kind), kind)
            return


class ThirdPartySkinWorker(QThread):
    '''读取第三方 Yggdrasil 账号皮肤并缓存到 SCL/skins。'''

    success = Signal(dict)
    error = Signal(str, str)
    def run(self):
        try:
            from core.net import HttpClient
            from core.skin import thirdparty_skin_cache_path
            auth = AuthManager()
            info = auth.fetch_thirdparty_profile()
            skin_path = ''
            if info.get('skin_url'):
                dest = thirdparty_skin_cache_path(info.get('uuid', ''), info.get('name', ''))
                try:
                    HttpClient(timeout=30).download(info['skin_url'], dest)
                except Exception:
                    pass
                if dest.is_file() and dest.stat().st_size > 64:
                    skin_path = str(dest)
            self.success.emit({**info, 'skin_path': skin_path})
        except Exception as exc:
            kind = str(getattr(exc, 'kind', '') or 'general')
            self.error.emit(friendly_auth_error(exc, kind), kind)
            return


class SkinSaveWorker(QThread):
    success = Signal(str)
    error = Signal(str)
    def __init__(self, urls, dest):
        super().__init__()
        self.urls = list(urls)
        self.dest = dest

    def run(self):
        from core.net import HttpClient
        last = ''
        for url in self.urls:
            if not url:
                continue
            try:
                HttpClient(timeout=20).download(url, self.dest)
                self.success.emit(self.dest)
                return
            except Exception as exc:
                last = str(exc)
                continue
        self.error.emit(f'保存皮肤失败：{last}')


class DeviceCodeDialog(FramelessDialog):
    '''正版登录代码弹窗：显示并自动复制登录代码。'''

    def __init__(self, code: str, uri: str, parent=None):
        super().__init__(parent, '正版账号登录')
        self.code = code or ''
        self.uri = uri or 'https://microsoft.com/link'
        self.setWindowModality(Qt.NonModal)
        self.setMinimumWidth(430)
        layout = self.content_layout
        message = QLabel(f'登录时的代码为：{self.code}，已自动复制到剪贴板。\n点击「确定」或「复制」会打开网页，请在网页中输入代码完成登录。')
        message.setWordWrap(True)
        message.setStyleSheet('color:rgba(255,255,255,0.8); font-size:13px;')
        layout.addWidget(message)
        code_label = QLabel(self.code)
        code_label.setAlignment(Qt.AlignCenter)
        code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code_label.setStyleSheet('color:#A8E6E6; font-size:28px; font-weight:900; background:rgba(136,221,221,0.12); border:1px solid rgba(136,221,221,0.4); border-radius:8px; padding:10px;')
        layout.addWidget(code_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.copy_btn = QPushButton('复制')
        self.copy_btn.setStyleSheet('QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:7px 20px; } QPushButton:hover { background:#4a4a4a; } QPushButton:disabled { background:#2f4d2f; color:#b9f0b9; }')
        self.copy_btn.clicked.connect(self._copy_code)
        self.ok_btn = QPushButton('确定')
        self.ok_btn.setStyleSheet('QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:6px; padding:7px 22px; } QPushButton:hover { background:#78CACA; }')
        self.ok_btn.clicked.connect(self._confirm)
        row.addWidget(self.copy_btn)
        row.addWidget(self.ok_btn)
        layout.addLayout(row)
        QApplication.clipboard().setText(self.code)

    def _copy_code(self):
        if self.code:
            QApplication.clipboard().setText(self.code)
        self.copy_btn.setText('复制成功')
        self.copy_btn.setEnabled(False)
        if self.uri:
            webbrowser.open(self.uri)

    def _confirm(self):
        if self.uri:
            webbrowser.open(self.uri)
        self.accept()


class AccountSwitchDialog(FramelessDialog):
    def __init__(self, accounts, current_id='', parent=None):
        super().__init__(parent, '切换账号')
        # 切换窗口只展示正版账号；第三方/离线账号在各自入口处理。
        self.accounts = [
            account for account in (accounts or [])
            if isinstance(account, dict) and account.get('type') == 'microsoft'
        ]
        self.deleted = False
        self._new_account_entry = None
        self._login_worker = None
        self._verification_uri = ''
        self._login_status = ''
        self._progress = 0
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(500)
        self._progress_timer.timeout.connect(self._tick_progress)
        self._code_dialog = None
        self.setMinimumSize(460, 420)
        layout = self.content_layout
        layout.setContentsMargins(18, 6, 18, 16)
        layout.setSpacing(10)
        title = QLabel('选择要登录的账号')
        title.setStyleSheet('font-size:16px; font-weight:900; color:#f0f0f0;')
        layout.addWidget(title)
        self.list = QListWidget()
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setStyleSheet('\n            QListWidget { background: rgba(30,41,41,0.62); border:1px solid rgba(255,255,255,0.16); border-radius:8px; }\n            QListWidget::item { padding:10px 12px; color:#ddd; border-bottom:1px solid rgba(255,255,255,0.05); }\n            QListWidget::item:selected { background: rgba(136,221,221,0.35); }\n        ')
        type_labels = {'microsoft': '正版账号', 'thirdparty': '第三方', 'offline': '离线'}
        current_row = 0
        for index, account in enumerate(self.accounts):
            kind = account.get('type', '')
            name = account.get('name', '未命名')
            detail = account.get('email') or account.get('api') or ''
            text = f'{name}    [{type_labels.get(kind, kind)}]'
            if detail:
                text += f'\n{detail}'
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, account)
            self.list.addItem(item)
            if account.get('id') == current_id:
                current_row = index
        new_item = QListWidgetItem('＋  正版登录\n登录一个新的正版账号')
        new_item.setData(Qt.UserRole, {'type': 'new'})
        _font = new_item.font()
        _font.setBold(True)
        new_item.setFont(_font)
        self.list.addItem(new_item)
        if self.accounts:
            self.list.setCurrentRow(current_row)
        else:
            self.list.setCurrentRow(0)
        layout.addWidget(self.list, 1)
        self.login_hint = QLabel('')
        self.login_hint.setWordWrap(True)
        self.login_hint.setStyleSheet('color:#A8E6E6; font-size:12px;')
        self.login_hint.hide()
        layout.addWidget(self.login_hint)
        self.open_login_btn = QPushButton('打开登录页面')
        self.open_login_btn.setStyleSheet('QPushButton { background:transparent; color:#A8E6E6; border:1px solid #A8E6E6; border-radius:6px; padding:5px 14px; }')
        self.open_login_btn.clicked.connect(self._open_login_page)
        self.open_login_btn.hide()
        layout.addWidget(self.open_login_btn, 0, Qt.AlignLeft)
        row = QHBoxLayout()
        self.delete_btn = QPushButton('删除')
        self.delete_btn.setStyleSheet('QPushButton { background:transparent; color:#ff7777; border:1px solid #ff7777; border-radius:6px; padding:6px 14px; }')
        self.delete_btn.clicked.connect(self._delete_current)
        row.addWidget(self.delete_btn)
        row.addStretch(1)
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.setStyleSheet('QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:6px 16px; }')
        self.cancel_btn.clicked.connect(self.reject)
        self.login_btn = QPushButton('登录')
        self.login_btn.setStyleSheet('QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:6px; padding:6px 20px; }QPushButton:disabled { background:#2c6f66; color:#cfe3f5; }')
        self.login_btn.clicked.connect(self._on_login_clicked)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.login_btn)
        layout.addLayout(row)

    def _on_login_clicked(self):
        account = self.selected_account()
        if account and account.get('type') == 'new':
            self._start_new_login()
            return
        self.accept()

    def _start_new_login(self):
        if self._login_worker is not None and self._login_worker.isRunning():
            return
        self._progress = 0
        self._login_status = '正在获取正版登录代码...'
        self.login_hint.setText(self._login_status)
        self.login_hint.show()
        self.open_login_btn.hide()
        self.login_btn.setEnabled(False)
        self.login_btn.setText('正在登录 0%')
        self.delete_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.list.setEnabled(False)
        self._progress_timer.start()
        worker = MicrosoftDeviceLoginWorker()
        self._login_worker = worker
        worker.code_ready.connect(self._on_new_login_code)
        worker.status_changed.connect(self._on_new_login_status)
        worker.success.connect(self._on_new_login_success)
        worker.error.connect(self._on_new_login_error)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_new_login_code(self, code: str, uri: str):
        self._verification_uri = uri or 'https://microsoft.com/link'
        self._progress = max(self._progress, 10)
        QApplication.clipboard().setText(code)
        self.login_hint.setText(f'登录代码：{code}（已自动复制）\n请在弹窗中点击「确定」或「复制」打开网页完成登录。')
        self.open_login_btn.show()
        self._update_progress_text()
        if self._code_dialog is not None:
            try:
                self._code_dialog.close()
            except RuntimeError:
                pass
        self._code_dialog = DeviceCodeDialog(code, self._verification_uri, self)
        self._code_dialog.show()
        self._code_dialog.raise_()
        self._code_dialog.activateWindow()

    def _close_code_dialog(self):
        dialog = self._code_dialog
        self._code_dialog = None
        if dialog is not None:
            try:
                dialog.close()
            except RuntimeError:
                pass

    def _on_new_login_status(self, message: str):
        self._login_status = message
        self.login_hint.setText(f'{message}\n登录按钮会显示总进度，登录成功后窗口会自动关闭。')

    def _tick_progress(self):
        if self._progress < 90:
            self._progress += 1
        self._update_progress_text()

    def _update_progress_text(self):
        self.login_btn.setText(f'正在登录 {self._progress}%')

    def _on_new_login_success(self, account: dict):
        self._progress_timer.stop()
        self._progress = 100
        self.login_btn.setText('登录成功 100%')
        self._close_code_dialog()
        try:
            auth = AuthManager()
            auth.save_microsoft_account(account)
            entry = None
            wanted = account.get('uuid') or account.get('name')
            for item in auth.get_saved_accounts():
                if item.get('type') == 'microsoft' and (item.get('uuid') == wanted or item.get('name') == account.get('name')):
                    entry = item
                    break
            if entry is None:
                entry = {'id': f'microsoft:{wanted}', 'type': 'microsoft', 'name': account.get('name', ''), 'uuid': account.get('uuid', ''), 'access_token': account.get('access_token', ''), 'refresh_token': account.get('refresh_token', ''), 'expires_at': account.get('expires_at', 0), 'avatar_url': account.get('avatar_url', '')}
            self._new_account_entry = entry
            self._select_account_entry(entry)
        except Exception as exc:
            self._on_new_login_error(f'保存账号失败：{exc}')
            return
        self.accept()

    def _select_account_entry(self, entry: dict):
        for row in range(self.list.count()):
            item = self.list.item(row)
            data = item.data(Qt.UserRole) or {}
            if data.get('id') == entry.get('id'):
                item.setData(Qt.UserRole, entry)
                item.setText(f'{entry.get("name", "未命名")}    [正版账号]\n已添加')
                self.list.setCurrentRow(row)
                break
        else:
            item = QListWidgetItem(f'{entry.get("name", "未命名")}    [正版账号]\n已添加')
            item.setData(Qt.UserRole, entry)
            self.list.insertItem(self.list.count() - 1, item)
            self.list.setCurrentRow(self.list.row(item))

    def _on_new_login_error(self, message: str, kind: str='general'):
        self._progress_timer.stop()
        self._login_worker = None
        self._close_code_dialog()
        self.login_btn.setEnabled(True)
        self.login_btn.setText('登录')
        self.delete_btn.setEnabled(True)
        self.list.setEnabled(True)
        self.login_hint.setText(f'登录失败：{message}')
        _show_microsoft_login_notice(self, message, kind)

    def _open_login_page(self):
        if self._verification_uri:
            webbrowser.open(self._verification_uri)

    def _cleanup_login(self):
        self._progress_timer.stop()
        self._close_code_dialog()
        worker = self._login_worker
        if worker is not None:
            try:
                if worker.isRunning():
                    worker.cancel()
                    worker.wait(2000)
                    if worker.isRunning():
                        worker.terminate()
                        worker.wait(300)
            except RuntimeError:
                pass
        self._login_worker = None

    def reject(self):
        if self._login_worker is not None and self._login_worker.isRunning():
            reply = QMessageBox.question(self, '取消登录', '正在登录新账号，确定取消吗？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            self._cleanup_login()
        super().reject()

    def closeEvent(self, event):
        if self._login_worker is not None and self._login_worker.isRunning():
            self._cleanup_login()
        self._progress_timer.stop()
        super().closeEvent(event)

    def _delete_current(self):
        item = self.list.currentItem()
        if not item:
            return
        account = item.data(Qt.UserRole)
        if not account or account.get('type') == 'new':
            return
        reply = QMessageBox.question(self, '删除账号', f'确定从列表删除账号「{account.get("name")}」吗？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        cfg = ConfigManager()
        accounts = [a for a in cfg.get('saved_accounts', []) if a.get('id') != account.get('id')]
        cfg.set('saved_accounts', accounts)
        self.accounts = accounts
        self.deleted = True
        self.list.takeItem(self.list.row(item))
        if self.list.count() == 0:
            self.reject()

    def selected_account(self):
        item = self.list.currentItem()
        if item:
            return item.data(Qt.UserRole)


class CapeDialog(FramelessDialog):
    '''展示从 Minecraft 官方接口获取的当前账号披风列表。'''

    def __init__(self, capes, current_id='', parent=None):
        super().__init__(parent, '选择披风')
        self.setFixedSize(520, 560)
        layout = self.content_layout
        layout.setContentsMargins(20, 6, 20, 16)
        hint = QLabel('列表来自当前正版账号，选择后立即应用到游戏。')
        hint.setStyleSheet('color:rgba(255,255,255,0.5); font-size:12px;')
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setStyleSheet('\n            QListWidget { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.1);\n                          border-radius:8px; outline:none; }\n            QListWidget::item { padding:7px 12px; color:#ddd; }\n            QListWidget::item:selected { background:rgba(136,221,221,0.35); }\n        ')
        none_item = QListWidgetItem('○  无披风')
        none_item.setData(Qt.UserRole, {'id': '', 'name': '无披风'})
        self.list.addItem(none_item)
        current_row = 0 if not current_id else -1
        for cape in capes:
            active = cape.get('state') == 'ACTIVE' or cape.get('id') == current_id
            if active:
                current_row = self.list.count()
            name = str(cape.get('name') or '披风').strip()
            item = QListWidgetItem(('●  ' if active else '○  ') + name)
            item.setData(Qt.UserRole, cape)
            item.setToolTip(name)
            self.list.addItem(item)
        if current_row < 0:
            current_row = 0
        self.list.setCurrentRow(current_row)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton('确定')
        ok.setStyleSheet('QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:6px; padding:7px 20px; }')
        ok.clicked.connect(self.accept)
        cancel = QPushButton('取消')
        cancel.setStyleSheet('QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:7px 20px; }')
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)

    def selected_cape(self) -> dict:
        item = self.list.currentItem()
        if not item:
            return {'id': '', 'name': '无披风'}
        return item.data(Qt.UserRole) or {'id': '', 'name': '无披风'}


class AccountPage(QWidget):
    account_changed = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('AccountPage')
        self.config = ConfigManager()
        self.auth = AuthManager()
        self._code_verifier = None
        self._oauth_state = None
        self._login_worker = None
        self._thirdparty_worker = None
        self._ms_silent_worker = None
        self._ms_device_worker = None
        self._ms_device_uri = ''
        self._ms_device_code = ''
        self._preview_worker = None
        self._preview_generation = 0
        self._preview_workers = set()
        self._action_workers = set()
        self._cape_worker = None
        self._cape_apply_worker = None
        self._tp_pending_username = ''
        self._tp_pending_password = ''
        self._tp_inline_worker = None
        self._tp_inline_pending_password = ''
        self._tp_inline_pending_username = ''
        self._tp_inline_pending_remember = False
        self._tp_inline_api = DEFAULT_LITTLESKIN_API
        self._tp_inline_switching = False
        self._tp_switch_previous_account = None
        self._tp_inline_login_token = 0
        self._ms_code_dialog = None
        self._ms_switching = False
        self._ms_login_progress_value = 0
        self._skin_refresh_token = 0
        self._skin_refresh_started_at = 0.0
        self._tp_server_anim = None
        self._section_toggles = {}
        self._section_hidden = {}
        self._section_collapsed = {}
        self._section_metrics = {}
        self._login_groups = []
        self._ms_profile_worker = None
        self._ms_saved_login_worker = None
        self._tp_skin_worker = None
        self._tp_skin_last_attempt = 0.0
        self._ms_icon_loader = SavedAccountIconLoader(self)
        self._ms_icon_loader.ready.connect(self._on_ms_account_icon_ready)
        self._init_ui()
        self._tp_settings_save_timer = QTimer(self)
        self._tp_settings_save_timer.setSingleShot(True)
        self._tp_settings_save_timer.setInterval(400)
        self._tp_settings_save_timer.timeout.connect(self._save_tp_server_settings)
        for edit in (
            self.tp_api_edit, self.tp_register_edit,
            self.tp_server_name_edit, self.tp_auto_server_edit,
        ):
            edit.textChanged.connect(
                lambda _text=None: self._tp_settings_save_timer.start()
            )
            edit.editingFinished.connect(self._save_tp_server_settings)
        self.tp_method_combo.currentIndexChanged.connect(
            lambda _index=None: self._tp_settings_save_timer.start()
        )
        self.tp_server_name_edit.textChanged.connect(
            lambda _text=None: self._update_thirdparty_tab_label()
        )
        self._ms_login_progress_timer = QTimer(self)
        self._ms_login_progress_timer.setInterval(520)
        self._ms_login_progress_timer.timeout.connect(self._tick_ms_login_progress)
        self._offline_name_timer = QTimer(self)
        self._offline_name_timer.setSingleShot(True)
        self._offline_name_timer.setInterval(500)
        self._offline_name_timer.timeout.connect(self._auto_update_offline_name)
        self.name_input.textChanged.connect((lambda: self._offline_name_timer.start()))
        self._ensure_valid_login_mode()
        self._refresh_account_info()
        self._refresh_offline_accounts()
        self._load_thirdparty_fields()
        self._ensure_current_account_saved()
        self.account_changed.connect(self._apply_login_section_order)
        self._apply_login_section_order()
        QTimer.singleShot(400, self.refresh_account_state)

    def _ensure_current_account_saved(self):
        try:
            login_type = self.config.get('login_type', 'offline')
            if login_type == 'microsoft':
                account = self.config.get('microsoft_account') or {}
                if account.get('name'):
                    account.setdefault(
                        'avatar_url',
                        f'https://mc-heads.net/avatar/{account["name"]}/96',
                    )
                    self.auth.save_microsoft_account(account)
            elif login_type == 'thirdparty':
                account = self.config.get('thirdparty_account') or {}
                if account.get('name'):
                    self.auth.save_thirdparty_account(account)
            elif self.config.get('player_name'):
                self.auth.save_offline_account(self.config.get('player_name'))
        except Exception:
            pass

    def _refresh_offline_accounts(self):
        combo = getattr(self, 'offline_name_combo', None)
        if combo is None:
            return
        names = []
        current = str(
            self.config.get('player_name', '')
            or self.config.get('offline_last_name', '')
            or self.auth.get_offline_name()
            or ''
        ).strip()
        if current:
            names.append(current)
        try:
            for entry in self.auth.get_saved_accounts():
                if not isinstance(entry, dict) or entry.get('type') != 'offline':
                    continue
                name = str(entry.get('name') or '').strip()
                if name and name not in names:
                    names.append(name)
        except Exception:
            pass
        combo.blockSignals(True)
        combo.clear()
        for name in names:
            combo.addItem(name)
        if current:
            index = combo.findText(current)
            combo.setCurrentIndex(index if index >= 0 else -1)
        else:
            combo.setCurrentIndex(-1)
        combo.setEditText(current)
        combo.blockSignals(False)
        self.name_input.setText(current)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet('\n            QScrollArea { background: transparent; border: none; }\n            QScrollBar:vertical { background: #2a2a2a; width: 12px; border-radius: 6px; }\n            QScrollBar::handle:vertical { background: #555; border-radius: 6px; min-height: 20px; }\n            QScrollBar::handle:vertical:hover { background: #777; }\n            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }\n        ')
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(24)
        title = QLabel('账号管理')
        title.setFont(ui_font(24, QFont.DemiBold))
        title.setStyleSheet('color: #f0f0f0;')
        title.hide()
        layout.addWidget(title)
        self._account_tab_mode = 'offline'
        self._last_tab_rendered = None
        self._tab_animation = None
        self._tab_effect_group = None
        self._account_tab_buttons = {}
        self._account_tab_icon_kinds = {}
        tab_bar = QWidget()
        tab_bar.setStyleSheet('background:transparent;')
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(0, 0, 0, 4)
        tab_layout.setSpacing(8)
        tab_layout.addStretch(1)
        tab_group = QButtonGroup(self)
        tab_group.setExclusive(True)
        for mode, label, icon_kind in (
            ('microsoft', '正版', 'microsoft'),
            ('offline', '离线', 'offline'),
            ('thirdparty', 'LittleSkin 登录', 'littleskin'),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(34)
            btn.setIcon(QIcon(login_icon(icon_kind, 18)))
            btn.setIconSize(QSize(18, 18))
            btn.setStyleSheet(
                'QPushButton { background:transparent; color:rgba(255,255,255,0.62);'
                ' border:none; border-radius:17px; padding:7px 20px;'
                ' font-size:14px; font-weight:700; text-align:center; }'
                'QPushButton:hover { background:rgba(255,255,255,0.08); }'
                'QPushButton:checked { background:#90D9D9; color:#122828; }'
            )
            btn.clicked.connect(
                (lambda _checked=False, m=mode: self._on_account_tab_clicked(m))
            )
            tab_group.addButton(btn)
            tab_layout.addWidget(btn)
            self._account_tab_buttons[mode] = btn
            self._account_tab_icon_kinds[mode] = icon_kind
        tab_layout.addStretch(1)
        layout.addWidget(tab_bar)
        # 账户卡和服务器卡放在同一个容器里，切换 tab 时一起做淡入动画。
        self.account_cards_host = QWidget()
        self.account_cards_host.setStyleSheet('background:transparent;')
        self.account_cards_layout = QVBoxLayout(self.account_cards_host)
        self.account_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.account_cards_layout.setSpacing(24)
        layout.addWidget(self.account_cards_host)
        group = RoundedGroupBox(radius=12)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(24, 22, 24, 20)
        group_layout.setSpacing(8)
        self.avatar = AvatarLabel(size=76, radius=10)
        self.avatar.set_skin_interactive(True)
        self.avatar.clicked.connect(self._change_skin)
        self.avatar.context_menu_requested.connect(self._show_avatar_menu)
        self.avatar.source_changed.connect(self.account_changed.emit)
        group_layout.addWidget(self.avatar, 0, Qt.AlignHCenter)
        self.player_name_label = QLabel('未登录')
        self.player_name_label.setAlignment(Qt.AlignCenter)
        self.player_name_label.setStyleSheet('color: rgba(255,255,255,0.92); font-size: 20px; font-weight:900;')
        group_layout.addWidget(self.player_name_label)
        self.email_label = QLabel('')
        self.email_label.setAlignment(Qt.AlignCenter)
        self.email_label.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 13px;')
        self.email_label.hide()
        group_layout.addWidget(self.email_label)
        self.login_type_label = QLabel('离线登录')
        self.login_type_label.setAlignment(Qt.AlignCenter)
        self.login_type_label.setStyleSheet('color: rgba(255,255,255,0.4); font-size: 12px;')
        self.login_type_label.hide()
        group_layout.addWidget(self.login_type_label)
        self.tp_actions_row = QWidget()
        self.tp_actions_row.setStyleSheet('background:transparent;')
        tp_actions = QHBoxLayout(self.tp_actions_row)
        tp_actions.setContentsMargins(0, 6, 0, 2)
        tp_actions.setSpacing(10)
        tp_actions.addStretch(1)
        self.tp_switch_btn = QPushButton('切换账号')
        self.tp_switch_btn.setMinimumHeight(34)
        self.tp_switch_btn.setStyleSheet('QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:7px; padding:6px 22px; font-size:13px; }QPushButton:hover { background:#78CACA; }')
        self.tp_switch_btn.clicked.connect(self._show_tp_inline_login)
        self.tp_logout_btn = QPushButton('退出登录')
        self.tp_logout_btn.setMinimumHeight(34)
        self.tp_logout_btn.setStyleSheet('QPushButton { background:#4a4a4a; color:white; border:none; border-radius:7px; padding:6px 22px; font-size:13px; }QPushButton:hover { background:#5a5a5a; }')
        self.tp_logout_btn.clicked.connect(self._on_logout)
        tp_actions.addWidget(self.tp_switch_btn)
        tp_actions.addWidget(self.tp_logout_btn)
        tp_actions.addStretch(1)
        self.tp_actions_row.hide()
        group_layout.addWidget(self.tp_actions_row)
        # 卡面内容容器：正版切号 / 离线输入 / 第三方登录都在这里原地切换，
        # 不再往页面下方追加新的登录卡片。
        self.mode_content = QWidget()
        self.mode_content.setStyleSheet('background:transparent;')
        self.mode_content_layout = QVBoxLayout(self.mode_content)
        self.mode_content_layout.setContentsMargins(0, 0, 0, 0)
        self.mode_content_layout.setSpacing(6)
        group_layout.addWidget(self.mode_content)
        self.tp_inline_wrap = QWidget()
        self.tp_inline_wrap.setMinimumWidth(460)
        self.tp_inline_wrap.setMaximumWidth(620)
        inline_wrap_layout = QVBoxLayout(self.tp_inline_wrap)
        inline_wrap_layout.setContentsMargins(0, 0, 0, 0)
        inline_wrap_layout.setSpacing(6)
        self.tp_inline_title = QLabel('第三方登录')
        self.tp_inline_title.setStyleSheet('color:rgba(255,255,255,0.62); font-size:13px; padding:0;')
        inline_wrap_layout.addWidget(self.tp_inline_title, 0, Qt.AlignLeft)
        self.tp_inline_panel = QWidget()
        self.tp_inline_panel.setObjectName('TPInlinePanel')
        self.tp_inline_panel.setAttribute(Qt.WA_StyledBackground, True)
        inline_wrap_layout.addWidget(self.tp_inline_panel)
        self.tp_inline_panel.setStyleSheet('\n            QWidget#TPInlinePanel {\n                background: rgba(18,23,23,0.72);\n                border: 1px solid rgba(255,255,255,0.16);\n                border-radius: 10px;\n            }\n            QWidget#TPInlinePanel QLabel { background: transparent; }\n        ')
        inline_layout = QVBoxLayout(self.tp_inline_panel)
        inline_layout.setContentsMargins(18, 14, 18, 14)
        inline_layout.setSpacing(9)
        inline_input_style = 'background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.16); border-radius:6px; color:#f0f0f0; padding:5px 9px;'
        email_row = QHBoxLayout()
        email_label = QLabel('邮箱')
        email_label.setFixedWidth(52)
        email_label.setStyleSheet('color:rgba(255,255,255,0.75); font-size:13px;')
        email_row.addWidget(email_label)
        self.tp_inline_email = HistoryComboBox()
        self.tp_inline_email.setEditable(True)
        self.tp_inline_email.setMinimumHeight(34)
        self.tp_inline_email.setStyleSheet(f'QComboBox {{ {inline_input_style} }}QComboBox QAbstractItemView {{ background:#24262c; color:#f0f0f0; selection-background-color:#88DDDD; border:1px solid rgba(255,255,255,0.14); }}')
        tp_email_edit = AutoSelectLineEdit(self.tp_inline_email)
        tp_email_edit.setStyleSheet(
            'background:transparent; border:none; color:#f0f0f0; padding:1px;'
        )
        tp_email_edit.setPlaceholderText('LittleSkin 邮箱或用户名')
        self.tp_inline_email.setLineEdit(tp_email_edit)
        self.tp_inline_email.activated.connect(self._on_tp_inline_account_changed)
        self.tp_inline_email.editTextChanged.connect(self._on_tp_inline_email_edited)
        email_row.addWidget(self.tp_inline_email, 1)
        inline_layout.addLayout(email_row)
        password_row = QHBoxLayout()
        password_label = QLabel('密码')
        password_label.setFixedWidth(52)
        password_label.setStyleSheet('color:rgba(255,255,255,0.75); font-size:13px;')
        password_row.addWidget(password_label)
        self.tp_inline_password = AutoSelectLineEdit()
        self.tp_inline_password.setEchoMode(QLineEdit.Password)
        self.tp_inline_password.setMinimumHeight(34)
        self.tp_inline_password.setStyleSheet(f'QLineEdit {{ {inline_input_style} }}')
        self.tp_inline_password.setPlaceholderText('密码')
        self.tp_inline_password.returnPressed.connect(self._on_tp_inline_login)
        password_row.addWidget(self.tp_inline_password, 1)
        self.tp_inline_eye = QToolButton()
        self.tp_inline_eye.setText('👁')
        self.tp_inline_eye.setToolTip('显示密码')
        self.tp_inline_eye.setCheckable(True)
        self.tp_inline_eye.setCursor(Qt.PointingHandCursor)
        self.tp_inline_eye.setFixedSize(34, 32)
        self.tp_inline_eye.setStyleSheet('QToolButton { background:transparent; color:#cfcfcf; border:1px solid rgba(255,255,255,0.15); border-radius:6px; font-size:15px; }QToolButton:hover { background:rgba(255,255,255,0.08); }QToolButton:checked { color:#A8E6E6; border-color:#A8E6E6; }')
        self.tp_inline_eye.toggled.connect(self._toggle_tp_inline_password)
        password_row.addWidget(self.tp_inline_eye)
        inline_layout.addLayout(password_row)
        remember_row = QHBoxLayout()
        self.tp_inline_remember = ThemedCheckBox('记住密码')
        remember_row.addWidget(self.tp_inline_remember)
        remember_row.addStretch(1)
        self.tp_inline_forgot_btn = QPushButton('忘记密码')
        self.tp_inline_forgot_btn.setCursor(Qt.PointingHandCursor)
        self.tp_inline_forgot_btn.setStyleSheet('QPushButton { background:transparent; color:#A8E6E6; border:none; padding:2px 0; }QPushButton:hover { color:#c7e4ff; text-decoration:underline; }')
        self.tp_inline_forgot_btn.clicked.connect(self._open_tp_forgot_password)
        remember_row.addWidget(self.tp_inline_forgot_btn)
        inline_layout.addLayout(remember_row)
        self.tp_inline_status = QLabel('')
        self.tp_inline_status.setWordWrap(True)
        self.tp_inline_status.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px;')
        inline_layout.addWidget(self.tp_inline_status)
        inline_action_row = QHBoxLayout()
        inline_action_row.addStretch(1)
        self.tp_inline_login_btn = QPushButton('登录')
        self.tp_inline_login_btn.setStyleSheet('QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:6px; padding:6px 22px; }QPushButton:hover { background:#78CACA; }QPushButton:disabled { background:#2c6f66; color:#cfe3f5; }')
        self.tp_inline_login_btn.clicked.connect(self._on_tp_inline_login)
        inline_action_row.addWidget(self.tp_inline_login_btn)
        inline_layout.addLayout(inline_action_row)
        self.tp_inline_wrap.hide()
        self.mode_content_layout.addWidget(self.tp_inline_wrap, 0, Qt.AlignHCenter)
        actions_row = QHBoxLayout()
        actions_row.addStretch(1)
        action_style = '\n            QPushButton { background:#3a3a3a; color:#8ee08e; border:none;\n                          border-radius:7px; font-size:16px; padding:4px 12px; }\n            QPushButton:hover { background:#4a4a4a; }\n        '
        self.skin_action_btn = QPushButton()
        self.skin_action_btn.setToolTip('皮肤 / 披风')
        self.skin_action_btn.setFixedSize(52, 34)
        set_button_icon(self.skin_action_btn, 'shirt', 20, '#7ee787')
        self.skin_action_btn.setIconSize(QSize(20, 20))
        self.skin_action_btn.setStyleSheet(action_style)
        self.skin_action_btn.clicked.connect(self._show_skin_menu)
        self.edit_action_btn = QPushButton()
        self.edit_action_btn.setToolTip('修改账号信息')
        self.edit_action_btn.setFixedSize(52, 34)
        set_button_icon(self.edit_action_btn, 'pencil', 20, '#7ee787')
        self.edit_action_btn.setIconSize(QSize(20, 20))
        self.edit_action_btn.setStyleSheet(action_style)
        self.edit_action_btn.clicked.connect(self._show_edit_menu)
        self.switch_action_btn = QPushButton()
        self.switch_action_btn.setToolTip('切换账号')
        self.switch_action_btn.setFixedSize(52, 34)
        set_button_icon(self.switch_action_btn, 'switch', 20, '#7ee787')
        self.switch_action_btn.setIconSize(QSize(20, 20))
        self.switch_action_btn.setStyleSheet(action_style)
        self.switch_action_btn.clicked.connect(self._show_ms_inline_switcher)
        actions_row.addWidget(self.skin_action_btn)
        actions_row.addWidget(self.edit_action_btn)
        actions_row.addWidget(self.switch_action_btn)
        actions_row.addStretch(1)
        group_layout.addLayout(actions_row)
        btn_row = QHBoxLayout()
        self.logout_btn = QPushButton('退出登录')
        self.logout_btn.clicked.connect(self._on_logout)
        self.logout_btn.hide()
        self.logout_btn.setStyleSheet('QPushButton { background: #6c6c6c; color: white; border: none; border-radius: 4px; padding: 6px 12px; } QPushButton:hover { background: #5a5a5a; }')
        btn_row.addStretch(1)
        btn_row.addWidget(self.logout_btn)
        btn_row.addStretch(1)
        group_layout.addLayout(btn_row)
        self.profile_group = group
        self.account_cards_layout.addWidget(group)
        self.offline_inline_widget = QWidget()
        self.offline_inline_widget.setMaximumWidth(520)
        offline_layout = QVBoxLayout(self.offline_inline_widget)
        offline_layout.setContentsMargins(0, 4, 0, 0)
        offline_layout.setSpacing(14)
        self.offline_name_combo = HistoryComboBox()
        self.offline_name_combo.setEditable(True)
        self.offline_name_combo.setMinimumHeight(40)
        self.offline_name_combo.setMinimumWidth(360)
        self.offline_name_combo.setMaxVisibleItems(10)
        self.offline_name_combo.setStyleSheet(
            'QComboBox { background: rgba(255,255,255,0.06);'
            ' border:1px solid rgba(255,255,255,0.16); border-radius:6px;'
            ' color:#f0f0f0; padding:5px 10px; font-size:13px; }'
            'QComboBox::drop-down { border:none; width:22px; }'
            'QComboBox QAbstractItemView { background:#24262c; color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.16);'
            ' selection-background-color:#88DDDD; }'
            'QComboBox QAbstractItemView::item { min-height:28px; padding:3px 8px; }'
        )
        offline_edit = AutoSelectLineEdit(self.offline_name_combo)
        offline_edit.setStyleSheet(
            'background:transparent; border:none; color:#f0f0f0; padding:1px;'
        )
        offline_edit.setPlaceholderText('输入玩家名称')
        offline_edit.editingFinished.connect(self._remember_offline_name)
        self.offline_name_combo.setLineEdit(offline_edit)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText('输入玩家名称')
        self.name_input.hide()
        self.offline_name_combo.currentTextChanged.connect(
            self.name_input.setText
        )
        self.name_input.editingFinished.connect(self._remember_offline_name)
        self.offline_name_combo.activated.connect(
            lambda _index: self._remember_offline_name()
        )
        offline_layout.addWidget(self.offline_name_combo)
        self.offline_group = self.offline_inline_widget
        self.mode_content_layout.addWidget(self.offline_inline_widget, 0, Qt.AlignHCenter)
        ms_group = RoundedGroupBox(radius=12)
        ms_layout = QVBoxLayout(ms_group)
        ms_layout.setContentsMargins(24, 20, 24, 20)
        ms_layout.setSpacing(10)
        self.ms_title_label = QLabel('正版登录')
        self.ms_title_label.setStyleSheet('color: rgba(255,255,255,0.85); font-size: 16px; font-weight:900;')
        self.ms_title_label.hide()
        ms_layout.addWidget(self.ms_title_label)
        self.ms_desc_label = QLabel('使用正版账号登录；已保存的账号会显示在下方列表中，优先使用保存的登录凭据直接登录。')
        self.ms_desc_label.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 13px;')
        self.ms_desc_label.setWordWrap(True)
        self.ms_desc_label.hide()
        ms_layout.addWidget(self.ms_desc_label)
        self.ms_status_label = QLabel('就绪，点击「正版登录」')
        self.ms_status_label.setAlignment(Qt.AlignCenter)
        self.ms_status_label.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 13px;')
        self.ms_status_label.setWordWrap(True)
        self.ms_device_code_label = QLabel('')
        self.ms_device_code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ms_device_code_label.setAlignment(Qt.AlignCenter)
        self.ms_device_code_label.setStyleSheet('color:#A8E6E6; font-size:26px; font-weight:900; background:rgba(136,221,221,0.12); border:1px solid rgba(136,221,221,0.4); border-radius:8px; padding:10px;')
        self.ms_device_code_label.hide()
        self.ms_accounts_row = QWidget()
        self.ms_accounts_row.setMinimumWidth(440)
        self.ms_accounts_row.setMaximumWidth(500)
        ms_accounts_layout = QHBoxLayout(self.ms_accounts_row)
        ms_accounts_layout.setContentsMargins(0, 0, 0, 0)
        ms_accounts_layout.setSpacing(10)
        self.ms_accounts_combo = AvatarComboBox()
        self.ms_accounts_combo.setMinimumHeight(42)
        self.ms_accounts_combo.setMinimumWidth(210)
        self.ms_accounts_combo.setMaximumWidth(360)
        self.ms_accounts_combo.setMaxVisibleItems(10)
        self.ms_accounts_combo.setStyleSheet('\n            QComboBox {\n                background: rgba(30,41,41,0.62); color:#f0f0f0;\n                border:1px solid rgba(255,255,255,0.16); border-radius:8px;\n                padding:5px 10px; font-size:13px;\n            }\n            QComboBox:hover { border-color: rgba(136,221,221,0.55); }\n            QComboBox::drop-down { border:none; width:22px; }\n            QComboBox QAbstractItemView {\n                background:#24262c; color:#f0f0f0; outline:none;\n                border:1px solid rgba(255,255,255,0.16);\n                selection-background-color:#88DDDD;\n            }\n            QComboBox QAbstractItemView::item { min-height:32px; padding:4px 8px; }\n        ')
        self.ms_accounts_combo.setFixedHeight(40)
        ms_accounts_layout.addWidget(self.ms_accounts_combo, 1)
        self.ms_saved_login_btn = QPushButton('登录')
        self.ms_saved_login_btn.setMinimumHeight(40)
        self.ms_saved_login_btn.setFixedWidth(112)
        self.ms_saved_login_btn.setStyleSheet('QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:6px; padding:8px 20px; } QPushButton:hover { background:#78CACA; } QPushButton:disabled { background:#3b3f3f; color:#c7c7c7; }')
        self.ms_saved_login_btn.clicked.connect(self._on_ms_saved_login)
        ms_accounts_layout.addWidget(self.ms_saved_login_btn)
        self.ms_device_login_btn = QPushButton('登录其他账号')
        self.ms_device_login_btn.setFixedWidth(126)
        self.ms_device_login_btn.setMinimumHeight(40)
        self.ms_device_login_btn.setStyleSheet('QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:6px; padding:8px 16px; } QPushButton:hover { background:#78CACA; } QPushButton:pressed { background:#5FAFAF; } QPushButton:disabled { background:#2c6f66; color:#dbeafe; }')
        self.ms_device_login_btn.clicked.connect(self._on_ms_device_login)
        ms_accounts_layout.addWidget(self.ms_device_login_btn)
        self.ms_inline_widget = QWidget()
        self.ms_inline_widget.setStyleSheet('background:transparent;')
        ms_inline_layout = QVBoxLayout(self.ms_inline_widget)
        ms_inline_layout.setContentsMargins(0, 6, 0, 0)
        ms_inline_layout.setSpacing(8)
        ms_inline_layout.addWidget(self.ms_status_label)
        ms_inline_layout.addWidget(self.ms_device_code_label)
        ms_inline_layout.addWidget(self.ms_accounts_row, 0, Qt.AlignHCenter)
        self.ms_links_row = QWidget()
        self.ms_links_row.setStyleSheet('background:transparent;')
        ms_links_layout = QHBoxLayout(self.ms_links_row)
        ms_links_layout.setContentsMargins(0, 2, 0, 0)
        ms_links_layout.setSpacing(18)
        ms_links_layout.addStretch(1)
        link_style = (
            'QPushButton { background:transparent; color:rgba(255,255,255,0.42);'
            ' border:none; padding:5px 8px; font-size:13px; }'
            'QPushButton:hover { color:#A8E6E6; }'
        )
        self.ms_purchase_btn = QPushButton('»  购买正版')
        self.ms_purchase_btn.setCursor(Qt.PointingHandCursor)
        self.ms_purchase_btn.setStyleSheet(link_style)
        self.ms_purchase_btn.clicked.connect(
            lambda: webbrowser.open(MICROSOFT_PURCHASE_URL)
        )
        ms_links_layout.addWidget(self.ms_purchase_btn)
        self.ms_official_btn = QPushButton('»  前往官网')
        self.ms_official_btn.setCursor(Qt.PointingHandCursor)
        self.ms_official_btn.setStyleSheet(link_style)
        self.ms_official_btn.clicked.connect(
            lambda: webbrowser.open(MICROSOFT_OFFICIAL_URL)
        )
        ms_links_layout.addWidget(self.ms_official_btn)
        ms_links_layout.addStretch(1)
        ms_inline_layout.addWidget(self.ms_links_row)
        self.ms_inline_widget.hide()
        self.mode_content_layout.addWidget(self.ms_inline_widget)
        self.ms_group = ms_group
        self.ms_group.setParent(self)
        self.ms_group.hide()
        tp_group = RoundedGroupBox(radius=12)
        tp_layout = QVBoxLayout(tp_group)
        tp_layout.setContentsMargins(24, 20, 24, 20)
        tp_layout.setSpacing(10)
        self.tp_title_label = QLabel('第三方登录（Authlib Injector / LittleSkin）')
        self.tp_title_label.setStyleSheet('color: rgba(255,255,255,0.85); font-size: 16px; font-weight:900;')
        self.tp_title_label.hide()
        tp_layout.addWidget(self.tp_title_label)
        self.tp_desc_label = QLabel('支持 LittleSkin 及任何兼容 Yggdrasil 的认证服务器，登录后自动启用 authlib-injector。')
        self.tp_desc_label.setWordWrap(True)
        self.tp_desc_label.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
        self.tp_desc_label.hide()
        tp_layout.addWidget(self.tp_desc_label)
        self.tp_advanced_panel = QWidget()
        self.tp_advanced_panel.setStyleSheet('background:transparent;')
        advanced_layout = QVBoxLayout(self.tp_advanced_panel)
        advanced_layout.setContentsMargins(0, 4, 0, 0)
        advanced_layout.setSpacing(12)
        server_input_style = (
            'QLineEdit { background: rgba(255,255,255,0.06);'
            ' border:1px solid rgba(255,255,255,0.16); border-radius:6px;'
            ' color:#f0f0f0; padding:6px 9px; font-size:13px; }'
            'QLineEdit:focus { border-color:#90D9D9; }'
            'QComboBox { background: rgba(255,255,255,0.06);'
            ' border:1px solid rgba(255,255,255,0.16); border-radius:6px;'
            ' color:#f0f0f0; padding:5px 9px; font-size:13px; }'
            'QComboBox:focus { border-color:#90D9D9; }'
            'QComboBox::drop-down { border:none; width:22px; }'
            'QComboBox QAbstractItemView { background:#24262c; color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.16);'
            ' selection-background-color:#88DDDD; }'
        )
        # 服务器设置表单保持一列标签、一列输入框，整体居中。
        self.tp_server_form = QWidget()
        form_layout = QGridLayout(self.tp_server_form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setHorizontalSpacing(14)
        form_layout.setVerticalSpacing(10)
        form_layout.setColumnMinimumWidth(0, 170)
        form_layout.setColumnStretch(1, 1)
        def add_tp_row(label, widget):
            row = form_layout.rowCount()
            label_widget = QLabel(label)
            label_widget.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label_widget.setStyleSheet(
                'color: rgba(255,255,255,0.72); font-size: 13px; background: transparent;'
            )
            widget.setMinimumHeight(36)
            form_layout.addWidget(label_widget, row, 0)
            form_layout.addWidget(widget, row, 1)
            return widget

        self.tp_method_combo = QComboBox()
        self.tp_method_combo.addItem('LittleSkin 登录', 'littleskin')
        self.tp_method_combo.addItem('自定义 Authlib Injector', 'custom')
        self.tp_method_combo.setStyleSheet(server_input_style)
        self.tp_method_combo.currentIndexChanged.connect(self._on_tp_method_changed)
        add_tp_row('登录方式', self.tp_method_combo)
        self.tp_api_edit = QLineEdit()
        self.tp_api_edit.setStyleSheet(server_input_style)
        self.tp_api_edit.setPlaceholderText('例如 https://example.com/api/yggdrasil')
        add_tp_row('认证服务器（必填）', self.tp_api_edit)
        self.tp_register_edit = QLineEdit()
        self.tp_register_edit.setStyleSheet(server_input_style)
        self.tp_register_edit.setPlaceholderText('例如 https://example.com/auth/register')
        add_tp_row('注册链接', self.tp_register_edit)
        self.tp_server_name_edit = QLineEdit()
        self.tp_server_name_edit.setStyleSheet(server_input_style)
        self.tp_server_name_edit.setPlaceholderText('例如 我的服务器')
        add_tp_row('服务器名称', self.tp_server_name_edit)
        self.tp_auto_server_edit = QLineEdit()
        self.tp_auto_server_edit.setStyleSheet(server_input_style)
        self.tp_auto_server_edit.setPlaceholderText('可选，例如 play.example.com 或 play.example.com:25565')
        add_tp_row('自动进入服务器', self.tp_auto_server_edit)
        self.tp_user_edit = QLineEdit()
        self.tp_user_edit.setStyleSheet(server_input_style)
        self.tp_user_edit.setPlaceholderText('LittleSkin 邮箱或用户名')
        self.tp_pass_edit = QLineEdit()
        self.tp_pass_edit.setEchoMode(QLineEdit.Password)
        self.tp_pass_edit.setStyleSheet(server_input_style)
        self.tp_pass_edit.setPlaceholderText('密码')
        pass_row = QHBoxLayout()
        pass_label = QLabel('密码')
        pass_label.setStyleSheet(
            'color: rgba(255,255,255,0.72); font-size: 13px; background: transparent;'
        )
        pass_row.addWidget(pass_label)
        pass_row.addWidget(self.tp_pass_edit, 1)
        self.tp_eye_btn = QToolButton()
        self.tp_eye_btn.setText('👁')
        self.tp_eye_btn.setToolTip('显示密码')
        self.tp_eye_btn.setCheckable(True)
        self.tp_eye_btn.setCursor(Qt.PointingHandCursor)
        self.tp_eye_btn.setFixedSize(34, 30)
        self.tp_eye_btn.setStyleSheet('QToolButton { background:transparent; color:#cfcfcf; border:1px solid rgba(255,255,255,0.15); border-radius:6px; font-size:15px; } QToolButton:hover { background:rgba(255,255,255,0.08); } QToolButton:checked { color:#A8E6E6; border-color:#A8E6E6; }')
        self.tp_eye_btn.toggled.connect(self._toggle_tp_password)
        pass_row.addWidget(self.tp_eye_btn)
        self.tp_legacy_panel = QWidget()
        legacy_layout = QVBoxLayout(self.tp_legacy_panel)
        legacy_layout.setContentsMargins(0, 0, 0, 0)
        legacy_layout.setSpacing(10)
        legacy_layout.addLayout(pass_row)
        self.tp_remember_check = ThemedCheckBox('记住账号和密码')
        self.tp_remember_check.setToolTip('仅保存在本机，并使用 Windows 当前用户加密')
        legacy_layout.addWidget(self.tp_remember_check)
        tp_btn_row = QHBoxLayout()
        set_little_btn = QPushButton('设置为 LittleSkin')
        set_little_btn.setStyleSheet('QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:8px 14px; text-align:left; min-width:150px; } QPushButton:hover { background:#4a4a4a; }')
        set_little_btn.clicked.connect(self._set_littleskin_defaults)
        open_reg_btn = QPushButton('打开注册页')
        open_reg_btn.setStyleSheet(set_little_btn.styleSheet())
        open_reg_btn.clicked.connect(self._open_tp_register)
        self.tp_login_btn = QPushButton('第三方登录')
        self.tp_login_btn.setStyleSheet('QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:6px; padding:8px 18px; } QPushButton:hover { background:#78CACA; }')
        self.tp_login_btn.clicked.connect(self._on_tp_login)
        form_layout.addWidget(
            set_little_btn, form_layout.rowCount(), 0, 1, 2, Qt.AlignLeft
        )
        advanced_layout.addWidget(self.tp_server_form)
        tp_btn_row.addWidget(open_reg_btn)
        tp_btn_row.addStretch(1)
        tp_btn_row.addWidget(self.tp_login_btn)
        legacy_layout.addLayout(tp_btn_row)
        self.tp_status_label = QLabel('')
        self.tp_status_label.setWordWrap(True)
        self.tp_status_label.setStyleSheet('color: rgba(255,255,255,0.55); font-size: 12px;')
        legacy_layout.addWidget(self.tp_status_label)
        self.tp_legacy_panel.setParent(self)
        self.tp_legacy_panel.hide()
        self.tp_server_header = QWidget()
        self.tp_server_header.setStyleSheet('background:transparent;')
        server_header_layout = QHBoxLayout(self.tp_server_header)
        server_header_layout.setContentsMargins(0, 4, 0, 4)
        server_header_layout.setSpacing(8)
        self.tp_server_button = QPushButton('服务器')
        self.tp_server_button.setCursor(Qt.PointingHandCursor)
        self.tp_server_button.setStyleSheet(
            'QPushButton { background:transparent; color:rgba(255,255,255,0.9);'
            ' border:none; padding:0; text-align:left;'
            ' font-size:14px; font-weight:900; }'
            'QPushButton:hover { color:#A8E6E6; }'
        )
        self.tp_server_button.clicked.connect(self._toggle_tp_server)
        server_header_layout.addWidget(
            self.tp_server_button, 0, Qt.AlignLeft | Qt.AlignVCenter
        )
        server_header_layout.addStretch(1)
        self.tp_server_chevron = RotatingChevronButton(
            size=20, collapsed_angle=-90, expanded_angle=0,
            light_color='#2F6F6F', dark_color='#A8E6E6',
        )
        self.tp_server_chevron.setToolTip('展开服务器设置')
        self.tp_server_chevron.clicked.connect(self._toggle_tp_server)
        server_header_layout.addWidget(
            self.tp_server_chevron, 0, Qt.AlignRight | Qt.AlignVCenter
        )
        self.tp_server_header.setCursor(Qt.PointingHandCursor)
        self.tp_server_header.installEventFilter(self)
        # 服务器卡不需要 QGroupBox 的标题占位，否则折叠时标题会偏下。
        self.tp_server_card = RoundedFrame(radius=12)
        self.tp_server_card.setStyleSheet('background:transparent;')
        server_card_layout = QVBoxLayout(self.tp_server_card)
        server_card_layout.setContentsMargins(24, 12, 24, 12)
        server_card_layout.setSpacing(8)
        server_card_layout.addWidget(self.tp_server_header)
        self.tp_advanced_panel.hide()
        server_card_layout.addWidget(self.tp_advanced_panel)
        self.tp_server_card.hide()
        self.tp_server_card.installEventFilter(self)
        self.tp_group = tp_group
        self.tp_group.setParent(self)
        self.tp_group.hide()
        self.account_cards_layout.addWidget(self.tp_server_card)
        self._login_groups = []
        self.tp_inline_title.hide()
        self._apply_login_section_order()
        layout.addStretch(1)
        scroll.setWidget(content)
        self.account_scroll = scroll
        main_layout.addWidget(scroll)

    def eventFilter(self, obj, event):
        try:
            if (
                obj is getattr(self, 'tp_server_header', None)
                and event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
            ):
                self._toggle_tp_server()
                return True
            if (
                obj is getattr(self, 'tp_server_card', None)
                and event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
            ):
                # 连卡片顶部内边距也算在可点击标题行内。
                if event.position().y() <= self.tp_server_header.geometry().bottom() + 8:
                    self._toggle_tp_server()
                    return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                group = self._group_for_header(obj)
                if group is not None:
                    self._toggle_section(group)
                    return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _install_collapsible(self, group, header, title):
        if header is not None:
            # 标题标签可点击：整条卡片标题区域都能展开/收起
            header.setCursor(Qt.PointingHandCursor)
            header.installEventFilter(self)
        btn = RotatingChevronButton(
            size=26, collapsed_angle=0, expanded_angle=180,
            light_color='#2F6F6F', dark_color='#A8E6E6',
        )
        btn.setToolTip(f'展开/收起「{title}」')
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect((lambda _=False, g=group: self._toggle_section(g)))
        layout = group.layout()
        if header is not None:
            layout.removeWidget(header)
            header.setWordWrap(True)
            header.setMinimumWidth(0)
            header.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            header.setMinimumHeight(26)
            header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            header.setStyleSheet('color: rgba(255,255,255,0.85); font-size: 14px; font-weight:900;')
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        if header is not None:
            row.addWidget(header, 1, Qt.AlignVCenter)
        else:
            row.addStretch(1)
        row.addWidget(btn, 0, Qt.AlignVCenter)
        layout.insertLayout(0, row)
        hidden = {}
        for child in group.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
            if child is btn or child is header:
                continue
            hidden[child] = child.isHidden()
        self._section_toggles[group] = btn
        self._section_hidden[group] = hidden
        self._section_collapsed[group] = False
        self._section_metrics[group] = (
            layout.contentsMargins(),
            layout.spacing(),
        )

    def _group_for_header(self, obj):
        for group, btn in self._section_toggles.items():
            try:
                row = group.layout().itemAt(0).layout()
                if row is not None:
                    for i in range(row.count()):
                        if row.itemAt(i).widget() is obj:
                            return group
            except Exception:
                continue
        return None

    def _toggle_section(self, group):
        collapsed = not self._section_collapsed.get(group, False)
        self._set_section_collapsed(group, collapsed, animate=True)

    def _section_header_height(self, group) -> int:
        layout = group.layout()
        margins = layout.contentsMargins()
        header_h = 30
        try:
            row = layout.itemAt(0).layout()
            if row is not None and row.count():
                widget = row.itemAt(0).widget()
                if widget is not None:
                    width = max(1, widget.width() or group.width())
                    try:
                        wrapped = (
                            widget.heightForWidth(width)
                            if widget.hasHeightForWidth()
                            else widget.sizeHint().height()
                        )
                    except Exception:
                        wrapped = widget.sizeHint().height()
                    header_h = max(
                        header_h, widget.sizeHint().height(),
                        wrapped, widget.height(),
                    )
        except Exception:
            pass
        btn = self._section_toggles.get(group)
        if btn is not None:
            header_h = max(header_h, btn.sizeHint().height())
        return margins.top() + header_h + margins.bottom()

    def _set_section_collapsed(self, group, collapsed: bool, animate: bool=True):
        previous = self._section_collapsed.get(group)
        if animate and previous == collapsed:
            return
        self._section_collapsed[group] = collapsed
        btn = self._section_toggles.get(group)
        if btn is not None:
            btn.set_expanded(not collapsed, animate=animate)
        children = self._section_hidden.get(group, {})
        layout = group.layout()
        metrics = self._section_metrics.get(group)
        if metrics is not None:
            layout.setContentsMargins(18, 8, 18, 8)
            layout.setSpacing(0)
        collapsed_height = self._section_header_height(group)
        if not collapsed and metrics is not None:
            margins = metrics[0]
            layout.setContentsMargins(
                margins.left(), margins.top(), margins.right(), margins.bottom()
            )
            layout.setSpacing(metrics[1])
        old_anim = self._section_anims.pop(group, None) if hasattr(self, '_section_anims') else None
        if old_anim is not None:
            try:
                old_anim.stop()
            except RuntimeError:
                pass
        def apply_children(visible: bool):
            for child, was_hidden in children.items():
                try:
                    child.setVisible(visible and not was_hidden)
                except RuntimeError:
                    continue

        if not animate:
            apply_children(not collapsed)
            if not collapsed:
                group.setMaximumHeight(16777215)
                return
            group.setMaximumHeight(collapsed_height)
            return
        layout = group.layout()
        if collapsed:
            full_height = max(group.height(), layout.sizeHint().height())
            group.setMaximumHeight(max(full_height, collapsed_height))
            anim = HeightAnimation(group, group)
            anim.setDuration(220)
            anim.setStartValue(max(full_height, collapsed_height))
            anim.setEndValue(collapsed_height)
            anim.setEasingCurve(QEasingCurve.InCubic)
            def finish_collapse():
                if not self._section_collapsed.get(group):
                    return
                apply_children(False)
                group.setMaximumHeight(collapsed_height)

            anim.finished.connect(finish_collapse)
        else:
            apply_children(True)
            group.setMaximumHeight(collapsed_height)
            target = max(collapsed_height, layout.sizeHint().height())
            anim = HeightAnimation(group, group)
            anim.setDuration(240)
            anim.setStartValue(collapsed_height)
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            def finish_expand():
                if self._section_collapsed.get(group):
                    return
                group.setMaximumHeight(16777215)

            anim.finished.connect(finish_expand)
        if not hasattr(self, '_section_anims'):
            self._section_anims = {}
        self._section_anims[group] = anim
        anim.start()

    def _apply_login_section_order(self):
        '''按登录模式切换同一张账户卡的内容，不清理其他模式的账号。'''

        login_type = self.config.get('login_type', 'offline')
        mode = (
            'microsoft' if login_type == 'microsoft'
            else 'thirdparty' if login_type == 'thirdparty'
            else 'offline'
        )
        previous_mode = self._last_tab_rendered
        self._account_tab_mode = mode
        if mode != 'microsoft':
            self._ms_switching = False
        if mode != 'thirdparty':
            self._tp_inline_switching = False
            self._tp_switch_previous_account = None

        ms_account = self.config.get('microsoft_account') or {}
        tp_account = self.config.get('thirdparty_account') or {}
        ms_logged = mode == 'microsoft' and bool(ms_account.get('name'))
        tp_logged = mode == 'thirdparty' and bool(tp_account.get('name'))
        show_ms_form = mode == 'microsoft' and (
            not ms_logged or bool(self._ms_switching)
        )
        show_tp_form = mode == 'thirdparty' and (
            not tp_logged or bool(self._tp_inline_switching)
        )

        # 卡面内容原地切换：三个模式共用这一张账户卡。
        self.profile_group.setVisible(True)
        self.offline_inline_widget.setVisible(mode == 'offline')
        self.ms_inline_widget.setVisible(show_ms_form)
        self.ms_status_label.hide()
        if mode == 'microsoft':
            if show_ms_form:
                # 切换账号/未登录时显示通用人物图标和添加账号界面。
                self.avatar.set_person_icon()
                self.player_name_label.hide()
                self.email_label.hide()
                self.login_type_label.hide()
            else:
                self.player_name_label.show()
                self.login_type_label.show()
                if ms_account.get('email'):
                    self.email_label.show()
        self.tp_inline_wrap.setVisible(show_tp_form)
        if mode == 'thirdparty':
            if show_tp_form:
                # 第三方登录/切号时，输入框直接占用原头像区域。
                self.avatar.hide()
                self.player_name_label.hide()
                self.email_label.hide()
                self.login_type_label.hide()
            else:
                self.avatar.show()
                self.player_name_label.show()
                self.login_type_label.show()
                if tp_account.get('email'):
                    self.email_label.show()
                else:
                    self.email_label.hide()
        self.tp_server_card.setVisible(mode == 'thirdparty')
        self._set_ms_actions_visible(ms_logged and not show_ms_form)
        self.logout_btn.setVisible(ms_logged and not show_ms_form)
        self.tp_actions_row.setVisible(tp_logged and not show_tp_form)

        for key, btn in self._account_tab_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(key == mode)
            icon_kind = self._account_tab_icon_kinds.get(key, 'thirdparty')
            btn.setIcon(QIcon(login_icon(
                icon_kind, 18, '#122828' if key == mode else '#8F96A3'
            )))
            btn.blockSignals(False)

        if previous_mode != mode:
            # 切换 tab 时同步各列表；同一 tab 内输入/登录不重建离线列表，
            # 避免打断正在编辑的玩家名。
            if mode == 'thirdparty':
                self._set_tp_server_expanded(False, animate=False)
            self._refresh_ms_accounts_list()
            self._refresh_offline_accounts()
            self._refresh_tp_inline_accounts()
        self._last_tab_rendered = mode
        if previous_mode is not None and previous_mode != mode and self.isVisible():
            self._animate_tab_target(self.account_cards_host)

    def _stop_tab_animation(self):
        anim = getattr(self, '_tab_animation', None)
        self._tab_animation = None
        if anim is not None:
            try:
                anim.stop()
                anim.deleteLater()
            except RuntimeError:
                pass
        group = getattr(self, '_tab_effect_group', None)
        self._tab_effect_group = None
        if group is not None:
            try:
                group.setGraphicsEffect(None)
            except RuntimeError:
                pass

    def _animate_tab_target(self, target):
        if target is None:
            return
        self._stop_tab_animation()
        effect = QGraphicsOpacityEffect(target)
        effect.setOpacity(0.0)
        target.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b'opacity', self)
        anim.setDuration(170)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def finish():
            if self._tab_animation is anim:
                self._tab_animation = None
                self._tab_effect_group = None
                try:
                    target.setGraphicsEffect(None)
                except RuntimeError:
                    pass
            try:
                anim.deleteLater()
            except RuntimeError:
                pass

        anim.finished.connect(finish)
        self._tab_animation = anim
        self._tab_effect_group = target
        anim.start()

    def _on_account_tab_clicked(self, mode):
        # 再次点击当前 tab 视为取消“切换账号”，回到账户卡本身。
        if mode == self._account_tab_mode:
            if mode == 'microsoft' and self._ms_switching:
                self._ms_switching = False
                self._refresh_account_info()
                self._apply_login_section_order()
                return
            if mode == 'thirdparty' and self._tp_inline_switching:
                # 取消切号：已经退出第三方，按正版/离线/未登录顺序回退。
                self._fallback_after_tp_logout()
                return
        self._switch_account_mode(mode)

    def _switch_account_mode(self, mode):
        '''切换启动使用的登录模式，保留其他模式的账号与 token。'''

        if mode == 'microsoft':
            account = self.config.get('microsoft_account') or {}
            if not account.get('name'):
                account = self.auth.get_last_microsoft_account_entry() or {}
            self.config.set('login_type', 'microsoft')
            self.config.set('microsoft_account', account or None)
            if account:
                try:
                    self.auth.save_microsoft_account(account)
                except Exception:
                    pass
        elif mode == 'thirdparty':
            account = self.config.get('thirdparty_account') or {}
            if not account.get('name'):
                for entry in self.auth.get_saved_accounts():
                    if isinstance(entry, dict) and entry.get('type') == 'thirdparty':
                        account = entry
                        break
            self.config.set('login_type', 'thirdparty')
            self.config.set('thirdparty_account', account or None)
        else:
            name = str(
                self.name_input.text()
                or self.config.get('player_name', '')
                or self.config.get('offline_last_name', '')
            ).strip()
            self.config.set('login_type', 'offline')
            if name:
                self.config.set('player_name', name)
        self.config.save()
        self._refresh_account_info()
        self._apply_login_section_order()
        self.account_changed.emit()

    def _current_account_id(self):
        login_type = self.config.get('login_type', 'offline')
        if login_type == 'microsoft':
            account = self.config.get('microsoft_account', {}) or {}
            return f'microsoft:{account.get("uuid") or account.get("name")}'
        if login_type == 'thirdparty':
            account = self.config.get('thirdparty_account', {}) or {}
            return f'thirdparty:{account.get("api")}:{account.get("uuid")}'
        return f'offline:{self.config.get("player_name", "")}'

    def _show_avatar_menu(self, pos):
        menu = QMenu(self)
        save_action = menu.addAction(
            QIcon(painted_icon('save', 16)), '另存为'
        )
        save_action.triggered.connect(self._save_current_skin)
        refresh_action = menu.addAction(
            QIcon(painted_icon('refresh', 16)), '刷新'
        )
        refresh_action.triggered.connect(self._refresh_current_skin)
        menu.exec(pos)

    def _show_skin_menu(self):
        menu = QMenu(self)
        menu.addAction('修改皮肤', self._change_skin)
        menu.addAction('刷新皮肤', self._refresh_current_skin)
        menu.addAction('保存皮肤文件', self._save_current_skin)
        menu.addSeparator()
        menu.addAction('修改披风', self._choose_cape)
        menu.addAction('刷新披风列表', self._refresh_cape_list)
        if self.config.get('login_type') == 'thirdparty':
            menu.addSeparator()
            menu.addAction('使用 CDKEY 兑换奖励', self._open_cdkey_page)
        menu.exec(self.skin_action_btn.mapToGlobal(self.skin_action_btn.rect().bottomLeft()))

    def _show_edit_menu(self):
        menu = QMenu(self)
        login_type = self.config.get('login_type', 'offline')
        if login_type == 'thirdparty':
            menu.addAction('修改密码', (lambda: self._open_account_page('password')))
            menu.addAction('修改玩家名', (lambda: self._open_account_page('profile')))
        elif login_type == 'microsoft':
            menu.addAction('修改密码', (lambda: self._open_account_page('password')))
            menu.addAction('修改玩家名', (lambda: self._open_account_page('profile')))
        else:
            menu.addAction('修改玩家名', (lambda: self.name_input.setFocus()))
        menu.exec(self.edit_action_btn.mapToGlobal(self.edit_action_btn.rect().bottomLeft()))

    def _show_ms_inline_switcher(self):
        '''正版切换账号：原地展开账号下拉，不弹新的切换窗口。'''

        self._ms_switching = True
        self._switch_account_mode('microsoft')
        self.profile_group.setVisible(True)
        self.ms_inline_widget.setVisible(True)
        # 已登录账号切换时只显示账号列表，去掉提示小字。
        self.ms_status_label.hide()
        self._refresh_ms_accounts_list()
        # 切换账号默认停在“添加新账号”，和未登录界面一致；
        # 已保存账号仍可从下拉列表中选。
        if self.ms_accounts_combo.count():
            self.ms_accounts_combo.setCurrentIndex(
                self.ms_accounts_combo.count() - 1
            )
        self._scroll_account_to_top()

    def _set_ms_login_progress(self, value):
        try:
            value = max(self._ms_login_progress_value, min(99, int(value)))
        except (TypeError, ValueError):
            return
        self._ms_login_progress_value = value
        try:
            self.ms_saved_login_btn.setEnabled(False)
            self.ms_saved_login_btn.setText(f'{value}%')
        except RuntimeError:
            pass

    def _begin_ms_login_progress(self, value=5, animate=False):
        self._ms_login_progress_value = max(0, min(99, int(value)))
        try:
            self.ms_saved_login_btn.setEnabled(False)
            self.ms_saved_login_btn.setText(f'{self._ms_login_progress_value}%')
        except RuntimeError:
            pass
        if animate:
            self._ms_login_progress_timer.start()
        else:
            self._ms_login_progress_timer.stop()

    def _tick_ms_login_progress(self):
        # 已保存账号刷新凭据没有细粒度回调，用固定阶段推进到 85% 后等待结果。
        current = self._ms_login_progress_value
        step = next((value for value in (25, 40, 55, 70, 85) if value > current), None)
        if step is None:
            self._ms_login_progress_timer.stop()
            return
        self._set_ms_login_progress(step)
        if step >= 85:
            self._ms_login_progress_timer.stop()

    def _finish_ms_login_progress(self, success=False, failed=False):
        self._ms_login_progress_timer.stop()
        try:
            self.ms_saved_login_btn.setEnabled(True)
            if success:
                self._ms_login_progress_value = 100
                self.ms_saved_login_btn.setText('登录成功')
                QTimer.singleShot(900, self._reset_ms_login_button_text)
            elif failed:
                self._ms_login_progress_value = 100
                self.ms_saved_login_btn.setText('登录失败')
                QTimer.singleShot(900, self._reset_ms_login_button_text)
            else:
                self._ms_login_progress_value = 0
                self.ms_saved_login_btn.setText('登录')
        except RuntimeError:
            pass

    def _reset_ms_login_button_text(self):
        if self._ms_login_progress_value < 100:
            return
        self._ms_login_progress_value = 0
        try:
            self.ms_saved_login_btn.setText('登录')
        except RuntimeError:
            pass

    def _show_account_switcher(self):
        accounts = [
            entry for entry in self.auth.get_saved_accounts()
            if isinstance(entry, dict) and entry.get('type') == 'microsoft'
        ]
        dialog = AccountSwitchDialog(accounts, self._current_account_id(), self)
        if dialog.exec() != QDialog.Accepted:
            if dialog.deleted:
                self._refresh_account_info()
            return
        entry = dialog.selected_account()
        if not entry:
            return
        newly_logged_in = dialog._new_account_entry is not None
        if not newly_logged_in:
            try:
                self.auth.switch_to_account(entry)
            except Exception as exc:
                kind = str(getattr(exc, 'kind', '') or '')
                message = friendly_auth_error(exc, kind)
                if entry.get('type') == 'microsoft':
                    if kind == 'expired':
                        self.ms_status_label.setText(message)
                        self.ms_status_label.setStyleSheet('color:#fbbf24; font-size:13px;')
                        self._start_ms_device_flow()
                        return
                    if kind in ('no_game', 'general'):
                        _show_microsoft_login_notice(self, message, kind)
                        return
                QMessageBox.critical(self, '切换失败', message)
                return
        self._refresh_account_info()
        self._refresh_ms_accounts_list()
        if self.config.get('login_type', 'offline') == 'microsoft':
            self._refresh_microsoft_profile_async(user_initiated=False)
        self.account_changed.emit()
        if entry.get('type') == 'microsoft' and not newly_logged_in:
            try:
                self.auth.get_login_options()
            except Exception:
                QMessageBox.information(self, '需要重新登录', '该正版账号的登录状态已过期，将重新进行正版登录验证。')
                self._start_ms_device_flow()
                return
        if not newly_logged_in:
            QMessageBox.information(self, '切换成功', f'已切换到账号：{entry.get("name")}')

    def _tp_inline_match_entry(self, username: str):
        username = str(username or '').strip()
        combo = self.tp_inline_email
        for index in range(combo.count()):
            entry = combo.itemData(index)
            if not isinstance(entry, dict):
                continue
            names = {str(entry.get('email') or '').strip(), str(entry.get('name') or '').strip(), combo.itemText(index).strip()}
            if username in names:
                return entry

    def _fill_tp_inline_entry(self, entry, username: str):
        entry = entry if isinstance(entry, dict) else {}
        username = str(username or entry.get('email') or entry.get('name') or '').strip()
        api = entry.get('api') or self.tp_api_edit.text().strip() or DEFAULT_LITTLESKIN_API
        self._tp_inline_api = api
        password = self.auth.get_thirdparty_saved_password(api, username)
        self.tp_inline_password.setText(password)
        self.tp_inline_remember.setChecked(bool(password))
        self.tp_inline_status.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px;')
        if password:
            self.tp_inline_status.setText('')
            return
        if entry.get('access_token'):
            self.tp_inline_status.setText('该账号已保存登录状态，密码留空可直接切换。')
            return
        self.tp_inline_status.setText('')

    def _refresh_tp_inline_accounts(self):
        combo = self.tp_inline_email
        combo.blockSignals(True)
        combo.clear()
        current = (
            getattr(self, '_tp_switch_previous_account', None)
            or self.config.get('thirdparty_account', {}) or {}
        )
        current_username = (current.get('email') or current.get('name') or '').strip()
        current_id = f'thirdparty:{current.get("api", "")}:{current.get("uuid", "")}' if current.get('name') else ''
        current_index = -1
        name_counts = {}
        for entry in self.auth.get_saved_accounts():
            if not isinstance(entry, dict) or entry.get('type') != 'thirdparty':
                continue
            username = (entry.get('email') or entry.get('name') or '').strip()
            if not username:
                continue
            label = username
            name_counts[username] = name_counts.get(username, 0) + 1
            if name_counts[username] > 1:
                server = entry.get('server_name') or urlparse(entry.get('api', '')).hostname or '第三方'
                label = f'{username}（{server}）'
            combo.addItem(label, entry)
            index = combo.count() - 1
            combo.setItemData(index, entry.get('server_name') or entry.get('api') or '', Qt.ToolTipRole)
            if entry.get('id') == current_id:
                current_index = index
        if current_index < 0 and current_username:
            combo.addItem(current_username, current)
            current_index = combo.count() - 1
        if current_index >= 0:
            combo.setCurrentIndex(current_index)
            combo.setEditText(current_username or combo.currentText())
            entry = combo.itemData(current_index) or current
            self._fill_tp_inline_entry(entry, current_username)
        else:
            combo.setEditText('')
            self._tp_inline_api = current.get('api') or self.tp_api_edit.text().strip() or DEFAULT_LITTLESKIN_API
            self.tp_inline_password.clear()
            self.tp_inline_remember.setChecked(False)
            self.tp_inline_status.setText('')
        combo.blockSignals(False)

    def _show_tp_inline_login(self):
        if self._tp_inline_worker is not None and self._tp_inline_worker.isRunning():
            return
        # 切换第三方账号会退出当前第三方登录；已保存账号仍保留在下拉列表中。
        previous = self.config.get('thirdparty_account') or {}
        self._tp_switch_previous_account = previous or None
        if self.config.get('thirdparty_account'):
            self.config.set('thirdparty_account', None)
            self.config.save()
        self._tp_inline_switching = True
        self._refresh_tp_inline_accounts()
        self._set_tp_server_expanded(False, animate=False)
        # 表单会替换头像/昵称区域；同时刷新侧边栏，避免仍显示已退出的账号。
        self.account_changed.emit()
        self.tp_inline_wrap.raise_()
        QTimer.singleShot(0, self._scroll_account_to_top)
        QTimer.singleShot(80, self._scroll_account_to_top)

    def _fallback_after_tp_logout(self):
        '''第三方登录未成功时：正版 > 离线 > 未登录（Steve/Alex）。'''

        self._tp_inline_switching = False
        self._tp_switch_previous_account = None
        self._tp_inline_login_token += 1
        self.tp_inline_login_btn.setEnabled(True)
        self.tp_inline_login_btn.setText('登录')
        self.tp_inline_wrap.hide()
        self._ms_switching = False
        ms = self.config.get('microsoft_account') or {}
        if not ms.get('name'):
            try:
                ms = self.auth.get_last_microsoft_account_entry() or {}
            except Exception:
                ms = {}
        if ms.get('name'):
            self.config.set('login_type', 'microsoft')
            self.config.set('microsoft_account', ms)
        else:
            name = ''
            try:
                name = self.auth.get_offline_name() or ''
            except Exception:
                name = ''
            if not name:
                name = str(
                    self.config.get('offline_last_name', '')
                    or self.config.get('player_name', '')
                ).strip()
            self.config.set('login_type', 'offline')
            if name:
                self.config.set('player_name', name)
        self.config.save()
        self._refresh_account_info()
        self._apply_login_section_order()
        self._refresh_offline_accounts()
        self.account_changed.emit()

    def _toggle_tp_server(self):
        # 用显式隐藏状态判断，父卡片隐藏时 isVisible() 会误判为收起。
        self._set_tp_server_expanded(self.tp_advanced_panel.isHidden(), animate=True)

    def _set_tp_server_expanded(self, expanded, animate=True):
        panel = self.tp_advanced_panel
        old_anim = getattr(self, '_tp_server_anim', None)
        if old_anim is not None:
            try:
                old_anim.stop()
            except RuntimeError:
                pass
            self._tp_server_anim = None
        expanded = bool(expanded)
        self.tp_server_chevron.set_expanded(expanded, animate=animate)
        self.tp_server_chevron.setToolTip(
            '收起服务器设置' if expanded else '展开服务器设置'
        )
        if not animate:
            panel.setMaximumHeight(16777215)
            panel.setVisible(expanded)
            return
        if expanded:
            was_hidden = panel.isHidden()
            panel.setVisible(True)
            current = 0 if was_hidden else max(1, panel.height())
            panel.setMaximumHeight(16777215)
            layout = panel.layout()
            if layout is not None:
                layout.activate()
            target = max(1, panel.sizeHint().height())
            panel.setMaximumHeight(current)
            animation = HeightAnimation(panel, self)
            animation.setDuration(220)
            animation.setStartValue(current)
            animation.setEndValue(target)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            def finish_expand():
                if self._tp_server_anim is animation:
                    panel.setMaximumHeight(16777215)
                    self._tp_server_anim = None
                try:
                    animation.deleteLater()
                except RuntimeError:
                    pass
            animation.finished.connect(finish_expand)
        else:
            if panel.isHidden():
                panel.setVisible(True)
            current = max(1, panel.height())
            animation = HeightAnimation(panel, self)
            animation.setDuration(180)
            animation.setStartValue(current)
            animation.setEndValue(0)
            animation.setEasingCurve(QEasingCurve.InCubic)
            def finish_collapse():
                if self._tp_server_anim is animation:
                    panel.setVisible(False)
                    panel.setMaximumHeight(16777215)
                    self._tp_server_anim = None
                try:
                    animation.deleteLater()
                except RuntimeError:
                    pass
            animation.finished.connect(finish_collapse)
        self._tp_server_anim = animation
        animation.start()

    def _scroll_account_to_top(self):
        try:
            self.account_scroll.verticalScrollBar().setValue(0)
        except RuntimeError:
            pass

    def _set_account_summary_visible(self, visible: bool):
        for widget in self.avatar, self.player_name_label, self.email_label, self.login_type_label, self.tp_actions_row:
            widget.setVisible(visible)
        if not visible:
            self._set_ms_actions_visible(False)
            self.logout_btn.hide()

    def _toggle_tp_inline_password(self, shown: bool):
        self.tp_inline_password.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        if shown:
            self.tp_inline_eye.setToolTip('隐藏密码')
            return
        self.tp_inline_eye.setToolTip('显示密码')

    def _on_tp_inline_account_changed(self, index: int):
        entry = self.tp_inline_email.itemData(index)
        if not isinstance(entry, dict):
            return
        username = (entry.get('email') or entry.get('name') or '').strip()
        self.tp_inline_email.blockSignals(True)
        self.tp_inline_email.setEditText(username)
        self.tp_inline_email.blockSignals(False)
        self._fill_tp_inline_entry(entry, username)

    def _on_tp_inline_email_edited(self, text: str):
        if self._tp_inline_match_entry(text):
            return
        if self.tp_inline_password.text():
            self.tp_inline_password.clear()
        self.tp_inline_remember.setChecked(False)
        self._tp_inline_api = self.tp_api_edit.text().strip() or DEFAULT_LITTLESKIN_API
        self.tp_inline_status.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px;')
        self.tp_inline_status.setText('')

    def _open_tp_forgot_password(self):
        entry = self._tp_inline_match_entry(self.tp_inline_email.currentText())
        api = (entry or {}).get('api') or self.tp_api_edit.text().strip() or self._tp_inline_api or DEFAULT_LITTLESKIN_API
        if 'littleskin.cn' in str(api).lower():
            webbrowser.open('https://littleskin.cn/auth/forgot')
            return
        parsed = urlparse(api)
        if parsed.scheme and parsed.netloc:
            webbrowser.open(f'{parsed.scheme}://{parsed.netloc}/auth/forgot')
            return
        webbrowser.open('https://littleskin.cn/auth/forgot')

    def _on_tp_inline_login(self):
        try:
            inline_busy = self._tp_inline_worker is not None and self._tp_inline_worker.isRunning()
        except RuntimeError:
            self._tp_inline_worker = None
            inline_busy = False
        if inline_busy:
            self.tp_inline_status.setStyleSheet('color:rgba(255,255,255,0.6); font-size:12px;')
            self.tp_inline_status.setText('正在登录中，请稍候...')
            return
        typed_username = self.tp_inline_email.currentText().strip()
        if not typed_username:
            self.tp_inline_status.setStyleSheet('color:#ff9c9c; font-size:12px;')
            self.tp_inline_status.setText('请输入邮箱或用户名。')
            self.tp_inline_email.setFocus()
            return
        entry = self._tp_inline_match_entry(typed_username)
        username = (entry.get('email') or entry.get('name') or typed_username).strip() if entry else typed_username
        if entry:
            api = entry.get('api') or self.tp_api_edit.text().strip() or DEFAULT_LITTLESKIN_API
        else:
            api = self.tp_api_edit.text().strip() or self._tp_inline_api or DEFAULT_LITTLESKIN_API
        api = api.rstrip('/')
        password = self.tp_inline_password.text()
        remember = self.tp_inline_remember.isChecked()
        if not password:
            if entry and entry.get('access_token'):
                try:
                    account = self.auth.switch_to_account(entry)
                except Exception as exc:
                    self.tp_inline_status.setStyleSheet('color:#ff9c9c; font-size:12px;')
                    self.tp_inline_status.setText(str(exc))
                    return
                if not remember:
                    self.auth.set_thirdparty_saved_password(api, username, '', False)
                self._tp_inline_switching = False
                self.tp_inline_wrap.hide()
                self._store_tp_server_settings_from_account(account)
                self._load_thirdparty_fields()
                self._refresh_account_info()
                self.account_changed.emit()
                QMessageBox.information(self, '登录成功', f'已登录第三方账号：{account.get("name", username)}')
                return
        if not password:
            self.tp_inline_status.setStyleSheet('color:#ff9c9c; font-size:12px;')
            self.tp_inline_status.setText('请输入密码；如果登录状态已失效，需要重新输入密码。')
            self.tp_inline_password.setFocus()
            return
        self._tp_inline_api = api
        self._tp_inline_pending_password = password
        self._tp_inline_pending_username = username
        self._tp_inline_pending_remember = remember
        self.tp_inline_login_btn.setEnabled(False)
        self.tp_inline_login_btn.setText('正在登录...')
        self.tp_inline_status.setStyleSheet('color:rgba(255,255,255,0.6); font-size:12px;')
        self.tp_inline_status.setText('正在登录第三方账号...')
        if entry:
            server_name = entry.get('server_name') or '第三方登录'
            register_url = entry.get('register_url') or ''
            auto_server = entry.get('auto_server') or ''
        else:
            server_name = self.tp_server_name_edit.text().strip() or ('LittleSkin 登录' if 'littleskin.cn' in api.lower() else urlparse(api).hostname or '第三方登录')
            register_url = self.tp_register_edit.text().strip() or (DEFAULT_LITTLESKIN_REGISTER if 'littleskin.cn' in api.lower() else '')
            auto_server = self.tp_auto_server_edit.text().strip()
        worker = ThirdPartyLoginWorker(api, username, password, server_name, register_url, auto_server)
        self._tp_inline_worker = worker
        self._tp_inline_login_token += 1
        token = self._tp_inline_login_token
        worker.success.connect(
            lambda account, t=token: self._on_tp_inline_success(account, t)
        )
        worker.error.connect(
            lambda msg, kind='general', t=token:
            self._on_tp_inline_error(msg, kind, t)
        )
        worker.finished.connect((lambda w=worker: self._on_tp_inline_worker_done(w)))
        worker.start()

    def _on_tp_inline_success(self, account, token=None):
        if token is not None and token != self._tp_inline_login_token:
            return
        self._tp_inline_login_token += 1
        self.tp_inline_login_btn.setEnabled(True)
        self.tp_inline_login_btn.setText('登录')
        api = self._tp_inline_api
        username = self._tp_inline_pending_username
        password = self._tp_inline_pending_password
        remember = getattr(self, '_tp_inline_pending_remember', False)
        self.auth.set_thirdparty_saved_password(api, username, password, remember)
        self._tp_inline_pending_password = ''
        self._tp_inline_switching = False
        self._tp_switch_previous_account = None
        self.tp_inline_wrap.hide()
        self._store_tp_server_settings_from_account(account)
        self._load_thirdparty_fields()
        self._refresh_account_info()
        self.account_changed.emit()
        QMessageBox.information(self, '登录成功', f'已登录第三方账号：{account.get("name", username)}')

    def _on_tp_inline_error(self, message, kind='general', token=None):
        if token is not None and token != self._tp_inline_login_token:
            return
        self._tp_inline_login_token += 1
        self.tp_inline_login_btn.setEnabled(True)
        self.tp_inline_login_btn.setText('登录')
        text = str(message or '第三方登录失败')
        self.tp_inline_status.setStyleSheet('color:#ff9c9c; font-size:12px;')
        self.tp_inline_status.setText(text)
        self._fallback_after_tp_logout()
        if kind == 'network':
            notify_network_failure(self, '验证登录', '当前网络问题导致无法登录，请使用加速器/VPN后重试！')
            return
        QMessageBox.critical(self, '第三方登录失败', text)

    def _on_tp_inline_worker_done(self, worker):
        if self._tp_inline_worker is worker:
            self._tp_inline_worker = None
        worker.deleteLater()

    def _change_skin(self):
        login_type = self.config.get('login_type', 'offline')
        if login_type == 'thirdparty':
            account = self.config.get('thirdparty_account', {}) or {}
            if 'littleskin.cn' in (account.get('api') or '').lower():
                webbrowser.open('https://littleskin.cn/user/closet')
                return
            webbrowser.open(account.get('api') or 'https://littleskin.cn/user/closet')
            return
        if login_type == 'offline':
            window = self.window()
            settings_page = getattr(window, 'settings_page', None)
            if settings_page is not None and hasattr(settings_page, 'open_offline_skin'):
                settings_page.open_offline_skin()
                switcher = getattr(window, '_switch_page', None)
                if callable(switcher):
                    switcher(3)
            else:
                QMessageBox.information(
                    self, '离线皮肤',
                    '请在 全局设置 → 启动 → 离线皮肤 中修改。',
                )
            return
        path, _ = QFileDialog.getOpenFileName(self, '选择皮肤 PNG', '', 'PNG 皮肤 (*.png)')
        if not path:
            return
        from core.skin import detect_skin_model, skin_size_error
        error = skin_size_error(path)
        if error:
            QMessageBox.warning(self, '皮肤文件不可用', error)
            return
        model = detect_skin_model(path)
        model_name = '纤细 (Alex)' if model == 'slim' else '经典 (Steve)'
        self.ms_status_label.setText(f'已识别为 {model_name}，正在上传到 Minecraft 官方...')
        self.ms_status_label.setStyleSheet('color: rgba(255,255,255,0.6); font-size:13px;')
        worker = SkinUploadWorker(path, model)
        worker.success.connect(self._on_skin_uploaded)
        worker.error.connect(self._on_skin_upload_error)
        self._start_action_worker(worker)

    def _on_skin_uploaded(self):
        self.ms_status_label.setText('皮肤已上传，正在从 Minecraft 官方刷新头像...')
        self.ms_status_label.setStyleSheet('color:#4ade80; font-size:13px;')
        self._refresh_microsoft_profile_async(user_initiated=True)
        QMessageBox.information(self, '成功', '皮肤已通过 Minecraft 官方接口上传，头像稍后会自动刷新。')

    def _on_skin_upload_error(self, message):
        message = friendly_auth_error(message)
        self.ms_status_label.setText(message)
        self.ms_status_label.setStyleSheet('color:#ff7777; font-size:13px;')
        QMessageBox.critical(self, '上传皮肤失败', message)

    def _refresh_current_skin(self):
        login_type = self.config.get('login_type', 'offline')
        if login_type == 'microsoft':
            self._refresh_microsoft_profile_async(user_initiated=True)
            return
        if login_type == 'thirdparty':
            self._refresh_thirdparty_skin_async(user_initiated=True)
            return
        token = self._begin_skin_refresh_feedback()
        self._refresh_offline_avatar()
        QTimer.singleShot(
            500,
            lambda t=token: self._finish_skin_refresh_feedback(
                True, token=t
            ),
        )

    def _save_current_skin(self):
        login_type = self.config.get('login_type', 'offline')
        local = None
        if login_type == 'offline':
            try:
                from core.skin import SkinManager
                local = SkinManager().preview_path()
            except Exception:
                local = None
        path, _ = QFileDialog.getSaveFileName(self, '保存皮肤文件', 'skin.png', 'PNG 皮肤 (*.png)')
        if not path:
            return
        if local and Path(local).is_file():
            try:
                shutil.copy2(local, path)
                QMessageBox.information(self, '保存成功', f'皮肤已保存到：\n{path}')
                return
            except OSError as exc:
                QMessageBox.critical(self, '保存失败', str(exc))
                return
        name = self.player_name_label.text()
        urls = [f'https://mc-heads.net/skin/{name}', f'https://minotar.net/skin/{name}']
        worker = SkinSaveWorker(urls, path)
        worker.success.connect((lambda p: QMessageBox.information(self, '保存成功', f'皮肤已保存到：\n{p}')))
        worker.error.connect((lambda msg: QMessageBox.critical(self, '保存失败', msg)))
        self._start_action_worker(worker)

    def _start_action_worker(self, worker):
        self._action_workers.add(worker)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect((lambda w=worker: self._action_workers.discard(w)))
        worker.start()

    def _choose_cape(self):
        login_type = self.config.get('login_type', 'offline')
        if login_type != 'microsoft':
            QMessageBox.information(self, '选择披风', '披风列表需要从 Minecraft 官方账号获取。\n请先切换到正版账号后再选择披风。')
            return
        if self._cape_worker is not None and self._cape_worker.isRunning():
            return
        self.ms_status_label.setText('正在从 Minecraft 官方获取披风列表...')
        worker = CapeListWorker()
        self._cape_worker = worker
        worker.success.connect(self._on_capes_loaded)
        worker.error.connect(self._on_cape_error)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_capes_loaded(self, capes):
        self._cape_worker = None
        self.ms_status_label.setText('披风列表已刷新')
        if not capes:
            QMessageBox.information(self, '选择披风', '当前账号没有可用的披风。')
            return
        current_id = ''
        for cape in capes:
            if cape.get('state') == 'ACTIVE':
                current_id = cape.get('id', '')
                break
        dialog = CapeDialog(capes, current_id, self)
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = dialog.selected_cape()
        if chosen.get('id', '') == current_id:
            self.ms_status_label.setText(f'当前披风：{chosen.get("name", "无披风")}')
            return
        self._apply_cape(chosen)

    def _apply_cape(self, cape: dict):
        if self._cape_apply_worker is not None and self._cape_apply_worker.isRunning():
            return
        self.ms_status_label.setText('正在切换披风...')
        worker = CapeApplyWorker(cape.get('id', ''), cape.get('name', '无披风'))
        self._cape_apply_worker = worker
        worker.success.connect(self._on_cape_applied)
        worker.error.connect(self._on_cape_error)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_cape_applied(self, name: str):
        self._cape_apply_worker = None
        self.ms_status_label.setText(f'披风已切换：{name}')
        self._refresh_account_info()

    def _on_cape_error(self, message: str):
        self._cape_worker = None
        self._cape_apply_worker = None
        self.ms_status_label.setText('披风操作失败')
        QMessageBox.critical(self, '披风操作失败', message)

    def _refresh_cape_list(self):
        self._choose_cape()

    def _open_profile_page(self):
        login_type = self.config.get('login_type', 'offline')
        if login_type == 'thirdparty':
            account = self.config.get('thirdparty_account', {}) or {}
            if 'littleskin.cn' in (account.get('api') or '').lower():
                webbrowser.open('https://littleskin.cn/user/closet')
                return
            webbrowser.open(account.get('api') or '')
            return
        if login_type == 'microsoft':
            webbrowser.open('https://www.minecraft.net/en-us/profile')
            return
        QMessageBox.information(self, '提示', '离线登录请使用下方离线皮肤设置。')

    def _open_cdkey_page(self):
        webbrowser.open('https://littleskin.cn/user/code')

    def _open_account_page(self, kind):
        login_type = self.config.get('login_type', 'offline')
        if login_type == 'thirdparty':
            if kind == 'password':
                webbrowser.open('https://littleskin.cn/user/setting')
                return
            webbrowser.open('https://littleskin.cn/user/profile')
            return
        if login_type == 'microsoft':
            webbrowser.open('https://account.microsoft.com/security' if kind == 'password' else 'https://www.minecraft.net/en-us/profile')

    def _thirdparty_display_name(self):
        return self.tp_server_name_edit.text().strip() or '第三方登录'

    def _update_thirdparty_tab_label(self):
        btn = getattr(self, '_account_tab_buttons', {}).get('thirdparty')
        if btn is None:
            return
        label = self._thirdparty_display_name()
        try:
            btn.setText(label)
            btn.setToolTip(label)
            account = self.config.get('thirdparty_account', {}) or {}
            if (
                self.config.get('login_type', 'offline') == 'thirdparty'
                and not account.get('name')
            ):
                self.login_type_label.setText(label)
        except RuntimeError:
            pass

    def _load_thirdparty_fields(self):
        account = self.config.get('thirdparty_account', {}) or {}
        saved = self.config.get('thirdparty_server_settings', None)
        have_saved = isinstance(saved, dict)
        if have_saved:
            api = str(saved.get('api') or '')
            register = str(saved.get('register_url') or '')
            server_name = str(saved.get('server_name') or '')
            auto_server = str(saved.get('auto_server') or '')
            method = str(saved.get('method') or '')
        elif account:
            api = str(account.get('api') or '')
            register = str(account.get('register_url') or '')
            server_name = str(account.get('server_name') or '')
            auto_server = str(account.get('auto_server') or '')
            method = ''
        else:
            # 首次使用不预填 LittleSkin 配置，让用户输入自己的服务器。
            api = register = server_name = auto_server = ''
            method = ''
        for edit, value in (
            (self.tp_api_edit, api),
            (self.tp_register_edit, register),
            (self.tp_server_name_edit, server_name),
            (self.tp_auto_server_edit, auto_server),
        ):
            edit.blockSignals(True)
            edit.setText(value)
            edit.blockSignals(False)
        if not method:
            # 首次没有配置时保持 LittleSkin 登录方式，但服务器字段留空；
            # 已填写非 LittleSkin 地址时自动识别为自定义服务器。
            method = 'littleskin' if not api or 'littleskin.cn' in api.lower() else 'custom'
        self.tp_method_combo.blockSignals(True)
        self.tp_method_combo.setCurrentIndex(0 if method == 'littleskin' else 1)
        self.tp_method_combo.blockSignals(False)
        if account and not have_saved and any((api, register, server_name, auto_server)):
            # 从旧版本升级时，把当前账号的服务器配置保存下来。
            self._save_tp_server_settings()
        self._update_thirdparty_tab_label()
        remember = bool(self.config.get('thirdparty_remember', False))
        self.tp_remember_check.setChecked(remember)
        if remember:
            self.tp_user_edit.setText(self.config.get('thirdparty_saved_username', '') or '')
            try:
                password = unprotect_secret(self.config.get('thirdparty_saved_password', '') or '')
            except Exception:
                password = ''
            self.tp_pass_edit.setText(password)

    def _save_tp_server_settings(self):
        data = {
            'method': self.tp_method_combo.currentData() or '',
            'api': self.tp_api_edit.text().strip(),
            'register_url': self.tp_register_edit.text().strip(),
            'server_name': self.tp_server_name_edit.text().strip(),
            'auto_server': self.tp_auto_server_edit.text().strip(),
        }
        self.config.set('thirdparty_server_settings', data, auto_save=False)
        self.config.save()
        self._update_thirdparty_tab_label()

    def _store_tp_server_settings_from_account(self, account):
        if not isinstance(account, dict):
            return
        api = str(account.get('api') or '').strip()
        if not api:
            return
        data = {
            'method': 'littleskin' if 'littleskin.cn' in api.lower() else 'custom',
            'api': api,
            'register_url': str(account.get('register_url') or '').strip(),
            'server_name': str(account.get('server_name') or '').strip(),
            'auto_server': str(account.get('auto_server') or '').strip(),
        }
        self.config.set('thirdparty_server_settings', data, auto_save=False)
        self.config.save()

    def _toggle_tp_password(self, shown: bool):
        self.tp_pass_edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        if shown:
            self.tp_eye_btn.setToolTip('隐藏密码')
            return
        self.tp_inline_eye.setToolTip('显示密码')

    def _on_tp_method_changed(self):
        if self.tp_method_combo.currentData() == 'littleskin':
            self._set_littleskin_defaults()

    def _set_littleskin_defaults(self):
        self.tp_method_combo.blockSignals(True)
        self.tp_method_combo.setCurrentIndex(0)
        self.tp_method_combo.blockSignals(False)
        self.tp_api_edit.setText(DEFAULT_LITTLESKIN_API)
        self.tp_register_edit.setText(DEFAULT_LITTLESKIN_REGISTER)
        self.tp_server_name_edit.setText('LittleSkin 登录')
        self.tp_status_label.setText('已填入 LittleSkin 默认配置')
        self._save_tp_server_settings()

    def _open_tp_register(self):
        url = self.tp_register_edit.text().strip()
        if url:
            webbrowser.open(url)

    def _on_tp_login(self):
        worker = self._thirdparty_worker
        try:
            if worker is not None and worker.isRunning():
                return
        except RuntimeError:
            self._thirdparty_worker = None
        api = self.tp_api_edit.text().strip()
        username = self.tp_user_edit.text().strip()
        password = self.tp_pass_edit.text()
        if not api or not username or not password:
            QMessageBox.warning(self, '提示', '请填写认证服务器、账号和密码')
            return
        self._tp_pending_username = username
        self._tp_pending_password = password
        self.tp_login_btn.setEnabled(False)
        self.tp_status_label.setText('正在登录第三方账号...')
        self._thirdparty_worker = ThirdPartyLoginWorker(api, username, password, self.tp_server_name_edit.text().strip() or '第三方登录', self.tp_register_edit.text().strip(), self.tp_auto_server_edit.text().strip())
        self._thirdparty_worker.success.connect(self._on_tp_success)
        self._thirdparty_worker.error.connect(self._on_tp_error)
        self._thirdparty_worker.finished.connect((lambda w=self._thirdparty_worker: self._on_tp_worker_done(w)))
        self._thirdparty_worker.finished.connect(self._thirdparty_worker.deleteLater)
        self._thirdparty_worker.start()

    def _on_tp_worker_done(self, worker):
        if self._thirdparty_worker is worker:
            self._thirdparty_worker = None

    def _on_tp_success(self, account):
        self.tp_login_btn.setEnabled(True)
        self._close_ms_code_dialog()
        self.tp_status_label.setText(f'登录成功：{account.get("name")}（{account.get("server_name")}）')
        username = getattr(self, '_tp_pending_username', '') or self.tp_user_edit.text().strip()
        password = getattr(self, '_tp_pending_password', '')
        api = self.tp_api_edit.text().strip().rstrip('/')
        self.auth.set_thirdparty_saved_password(api, username, password, self.tp_remember_check.isChecked())
        if not self.tp_remember_check.isChecked():
            self.tp_pass_edit.clear()
        self._tp_pending_password = ''
        self._tp_inline_switching = False
        self.tp_inline_wrap.hide()
        self._store_tp_server_settings_from_account(account)
        self._refresh_account_info()
        self.account_changed.emit()
        QMessageBox.information(self, '登录成功', f'已登录第三方账号：{account.get("name")}')

    def _on_tp_error(self, message, kind='general'):
        self.tp_login_btn.setEnabled(True)
        self._tp_pending_password = ''
        self.tp_status_label.setText(message)
        self.tp_status_label.setStyleSheet('color: #ff7777; font-size: 12px;')
        if kind == 'network':
            self.tp_status_label.setText('未连接到互联网')
            notify_network_failure(self, '验证登录', '当前网络问题导致无法登录，请使用加速器/VPN后重试！')
            return
        QMessageBox.critical(self, '第三方登录失败', message)

    def focus_offline_name(self):
        self.offline_name_combo.setFocus()
        line_edit = self.offline_name_combo.lineEdit()
        if line_edit is not None:
            line_edit.selectAll()
        QTimer.singleShot(0, self._scroll_to_offline_input)
        QTimer.singleShot(80, self._scroll_to_offline_input)

    def _scroll_to_offline_input(self):
        try:
            self.account_scroll.ensureWidgetVisible(self.profile_group, 20, 20)
        except RuntimeError:
            pass

    def shutdown(self):
        try:
            self._remember_offline_name()
        except Exception:
            pass
        try:
            if self._tp_settings_save_timer.isActive():
                self._tp_settings_save_timer.stop()
                self._save_tp_server_settings()
        except Exception:
            pass
        self._close_ms_code_dialog()
        workers = [self._login_worker, self._thirdparty_worker, self._preview_worker, self._ms_silent_worker, self._ms_device_worker, self._ms_profile_worker, self._ms_saved_login_worker, self._tp_skin_worker, self._tp_inline_worker, self._cape_worker, self._cape_apply_worker]
        workers.extend(list(self._preview_workers))
        workers.extend(list(self._action_workers))
        for worker in workers:
            try:
                if worker is not None and worker.isRunning():
                    wait_ms = 5000 if worker is self._ms_silent_worker else 1000
                    worker.wait(wait_ms)
                    if worker.isRunning():
                        worker.terminate()
                        worker.wait(300)
            except RuntimeError:
                continue
        self._preview_workers.clear()
        self._action_workers.clear()

    def _set_ms_actions_visible(self, visible: bool):
        for btn in self.skin_action_btn, self.edit_action_btn, self.switch_action_btn:
            btn.setVisible(visible)

    def _ensure_valid_login_mode(self):
        '''没有可用账号时默认回到离线模式，避免启动时停在空的正版/第三方页。'''

        login_type = str(self.config.get('login_type', 'offline') or 'offline')
        if login_type == 'microsoft':
            account = self.config.get('microsoft_account') or {}
            if not account.get('name'):
                saved = self.auth.get_last_microsoft_account_entry()
                if saved:
                    self.config.set('microsoft_account', saved)
                else:
                    self.config.set('login_type', 'offline')
                    self.config.save()
        elif login_type == 'thirdparty':
            account = self.config.get('thirdparty_account') or {}
            if not account.get('name'):
                saved = next(
                    (
                        entry for entry in self.auth.get_saved_accounts()
                        if isinstance(entry, dict)
                        and entry.get('type') == 'thirdparty'
                    ),
                    None,
                )
                if saved:
                    self.config.set('thirdparty_account', saved)
                else:
                    self.config.set('login_type', 'offline')
                    self.config.save()

    def _show_empty_profile(self, label):
        self.player_name_label.setText('未登录')
        self.player_name_label.show()
        self.login_type_label.setText(label)
        self.login_type_label.show()
        self.email_label.hide()
        self.tp_actions_row.hide()
        self.logout_btn.hide()
        self._set_ms_actions_visible(False)
        head = default_head_path()
        if head.is_file():
            self.avatar.set_head_file(str(head))
        else:
            self.avatar.set_image_url([])

    def _refresh_account_info(self):
        if getattr(self, '_tp_inline_switching', False):
            return
        try:
            login_type = self.config.get('login_type', 'offline')
            self.avatar.show()
            self.player_name_label.show()
            self.login_type_label.show()
            if login_type != 'thirdparty':
                self.tp_inline_wrap.hide()
            self._set_ms_actions_visible(False)
            self.tp_actions_row.hide()
            self.logout_btn.hide()
            remembered_name = self.config.get('offline_last_name', '') or self.auth.get_offline_name()
            if remembered_name and not self.name_input.text().strip():
                self.name_input.setText(remembered_name)
            if login_type == 'microsoft':
                account = self.config.get('microsoft_account', {})
                if not (account and account.get('name')):
                    self._show_empty_profile('正版账号')
                    self.ms_status_label.setText('请登录正版账号')
                    return
                if account and account.get('name'):
                    self._set_ms_actions_visible(True)
                    name = account.get('name', 'Steve')
                    self.player_name_label.setText(name)
                    self.login_type_label.setText('正版账号')
                    self.email_label.hide()
                    self.logout_btn.show()
                    try:
                        from core.skin import microsoft_skin_cache_path
                        cached = microsoft_skin_cache_path(account.get('uuid', ''), name)
                    except Exception:
                        cached = Path()
                    if cached.is_file():
                        self.avatar.set_skin_file(str(cached))
                    else:
                        self._set_microsoft_avatar_urls(account)
                    self.ms_status_label.setText('正版账号已登录')
                    return
            if login_type == 'thirdparty':
                account = self.config.get('thirdparty_account', {}) or {}
                if not account.get('name'):
                    self._show_empty_profile(self._thirdparty_display_name())
                    return
                if account.get('name'):
                    name = account.get('name')
                    email = account.get('email') or ''
                    self.player_name_label.setText(name)
                    self.login_type_label.setText(f'第三方登录 · {account.get("server_name", "Authlib Injector")}')
                    if email:
                        self.email_label.setText(email)
                        self.email_label.show()
                    else:
                        self.email_label.hide()
                    self.tp_actions_row.show()
                    self.logout_btn.hide()
                    cached_skin = None
                    try:
                        from core.skin import thirdparty_skin_cache_path
                        cached_skin = thirdparty_skin_cache_path(account.get('uuid', ''), name)
                    except Exception:
                        cached_skin = None
                    if cached_skin is not None and cached_skin.is_file():
                        self.avatar.set_skin_file(str(cached_skin))
                    else:
                        from core.skin import avatar_urls_for_account
                        try:
                            from core.paths import scl_dir
                            key = str(account.get('uuid') or account.get('name') or 'unknown')
                            cache_path = scl_dir() / 'icon_cache' / f'thirdparty_avatar_{key}.png'
                        except Exception:
                            cache_path = None
                        steve = Path(__file__).resolve().parent.parent / 'imgs' / 'steve_head.png'
                        self.avatar.set_image_url(
                            avatar_urls_for_account(account),
                            fallback_head=steve,
                            cache_path=cache_path,
                        )
                        self._refresh_thirdparty_skin_async(user_initiated=False)
                    self.tp_status_label.setText(f'已登录：{name}（{account.get("api", "")}）')
                    return
            player_name = self.auth.get_offline_name()
            if player_name:
                self.player_name_label.setText(player_name)
                self.login_type_label.setText('离线登录')
                self.email_label.hide()
                self.name_input.setText(player_name)
                self.logout_btn.hide()
                self._refresh_offline_avatar()
            else:
                self.player_name_label.setText('未登录')
                self.login_type_label.setText('')
                self.email_label.hide()
                self.logout_btn.hide()
                head = default_head_path()
                if head.is_file():
                    self.avatar.set_head_file(str(head))
                else:
                    self.avatar.set_image_url(['https://mc-heads.net/avatar/MHF_Steve/96', 'https://minotar.net/helm/MHF_Steve/96.png'])
            self.ms_status_label.setText('就绪，点击「正版登录」')
        except Exception as e:
            print(f'刷新账号信息异常: {e}')
            return

    def _remember_offline_name(self):
        try:
            name = self.name_input.text().strip()
        except RuntimeError:
            return
        if name:
            self.config.set('offline_last_name', name)
        self._auto_update_offline_name()

    @staticmethod
    def _offline_name_error(name: str) -> str:
        name = (name or '').strip()
        if not name:
            return '请输入玩家名称'
        if len(name) < 3 or len(name) > 16:
            return '玩家名长度需在 3-16 个字符之间'
        if not name.replace('_', '').isalnum():
            return '玩家名只能包含字母、数字和下划线'
        return ''

    def _auto_update_offline_name(self):
        '''输入完离线昵称后自动应用，无需再点“使用离线模式”。'''

        try:
            name = self.name_input.text().strip()
        except RuntimeError:
            return
        if not name:
            return
        if self._offline_name_error(name):
            return
        if self.config.get('login_type', 'offline') != 'offline':
            self.config.set('offline_last_name', name)
            return
        if name == (self.config.get('player_name', '') or ''):
            self.config.set('offline_last_name', name)
            return
        self.config.set('login_type', 'offline')
        self.config.set('player_name', name)
        self.config.set('offline_last_name', name)
        self.config.set('offline_name_set', True)
        try:
            self.auth.save_offline_account(name)
        except Exception:
            pass
        combo = getattr(self, 'offline_name_combo', None)
        if combo is not None and combo.findText(name) < 0:
            combo.addItem(name)
        self.player_name_label.setText(name)
        self.login_type_label.setText('离线登录')
        # 换昵称只改账号名，不重新计算/覆盖离线皮肤或正版皮肤。
        self.account_changed.emit()

    def current_avatar_source(self):
        """返回账户页当前展示的完整皮肤/头像源，供侧边栏复用。"""
        try:
            if self._tp_inline_switching:
                return None
            return self.avatar.head_source()
        except RuntimeError:
            return None

    def _refresh_offline_avatar(self, player_name: str=''):
        '''离线头像只由离线皮肤设置决定，不随昵称变化。'''

        try:
            from core.skin import SkinManager
            manager = SkinManager()
            settings = manager.get_settings()
            preview = manager.preview_path()
            if preview and Path(preview).is_file():
                self.avatar.set_skin_file(preview)
                return
            # 正版皮肤名已填写但本地还没有缓存时，先显示同名网络头像，
            # 同时后台下载完整皮肤；否则主界面会一直退回随机默认头。
            if settings.get('mode') == 'premium':
                name = str(settings.get('premium_name') or '').strip()
                if name:
                    head = default_head_path()
                    self.avatar.set_image_url(
                        [f'https://mc-heads.net/avatar/{name}/96',
                         f'https://minotar.net/helm/{name}/96.png'],
                        fallback_head=head if head.is_file() else None,
                    )
                    self._start_offline_skin_preview(settings)
                    return
        except Exception:
            pass
        head = default_head_path()
        if head.is_file():
            self.avatar.set_head_file(str(head))
        else:
            steve_head = Path(__file__).resolve().parent.parent / 'imgs' / 'steve_head.png'
            self.avatar.set_image_url(
                ['https://mc-heads.net/avatar/MHF_Steve/96',
                 'https://minotar.net/helm/MHF_Steve/96.png'],
                fallback_head=steve_head,
            )

    def _start_offline_skin_preview(self, settings):
        self._preview_generation += 1
        generation = self._preview_generation
        worker = SkinPreviewWorker(settings)
        worker.done.connect(
            lambda path, gen=generation: self._on_offline_skin_preview(gen, path)
        )
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(
            lambda w=worker: self._preview_workers.discard(w)
        )
        self._preview_workers.add(worker)
        self._preview_worker = worker
        worker.start()

    def _on_offline_skin_preview(self, generation, path):
        if generation != self._preview_generation:
            return
        if self.config.get('login_type', 'offline') != 'offline':
            return
        if not path or not Path(path).is_file():
            return
        self.avatar.set_skin_file(path)

    def _on_offline_login(self):
        self._close_ms_code_dialog()
        name = self.name_input.text().strip()
        error = self._offline_name_error(name)
        if error:
            QMessageBox.warning(self, '提示', error)
            return
        self.config.set('login_type', 'offline')
        self.config.set('player_name', name)
        self.config.set('offline_last_name', name)
        self.config.set('offline_name_set', True)
        self.auth.save_offline_account(name)
        self._tp_inline_switching = False
        self.tp_inline_wrap.hide()
        self._refresh_offline_accounts()
        self._refresh_account_info()
        self.account_changed.emit()
        QMessageBox.information(self, '成功', f'已切换为离线登录：{name}')

    def refresh_account_state(self):
        '''启动器和账号页显示时自动刷新头像、登录状态和账号列表。'''

        self._refresh_account_info()
        self._refresh_ms_accounts_list()
        if self.config.get('login_type', 'offline') == 'microsoft':
            self._refresh_microsoft_profile_async(user_initiated=False)
            return
        if self.config.get('login_type', 'offline') == 'thirdparty':
            self._refresh_thirdparty_skin_async(user_initiated=False)

    @staticmethod
    def _combo_icon(pixmap):
        '''为 QComboBox 提供物理分辨率更高的图标，避免高 DPI 下头像发糊。'''
        if pixmap is None or pixmap.isNull():
            return QIcon()
        from ui.widgets.settings_widgets import device_pixel_ratio
        dpr = max(1.0, float(device_pixel_ratio()))
        size = max(42, int(round(42 * dpr)))
        if (
            pixmap.width() != size
            or pixmap.height() != size
            or pixmap.devicePixelRatio() != 1.0
        ):
            pixmap = pixmap.scaled(
                size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            pixmap.setDevicePixelRatio(1.0)
        return QIcon(AvatarLabel._rounded(pixmap, int(round(10 * dpr))))

    def _ms_entry_icon(self, entry: dict):
        # 优先使用完整 64x64 皮肤裁头，确保第二层（帽子/外套）也参与合成。
        try:
            from core.skin import microsoft_skin_cache_path
            path = microsoft_skin_cache_path(entry.get('uuid', ''), entry.get('name', ''))
            if path.is_file():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    return self._combo_icon(AvatarLabel._head_from_skin(pix, 42))
        except Exception:
            pass
        # 没有完整皮肤时再用已经裁好的头像缓存。
        try:
            from core.paths import scl_dir
            key = str(entry.get('uuid') or entry.get('name') or 'unknown')
            avatar_path = scl_dir() / 'icon_cache' / f'microsoft_avatar_{key}.png'
            if avatar_path.is_file():
                pix = QPixmap(str(avatar_path))
                if not pix.isNull():
                    return self._combo_icon(pix)
        except Exception:
            pass
        steve = Path(__file__).resolve().parent.parent / 'imgs' / 'steve_head.png'
        if steve.is_file():
            pix = QPixmap(str(steve))
            if not pix.isNull():
                return self._combo_icon(pix)
        return QIcon()

    def _refresh_ms_accounts_list(self):
        if not hasattr(self, 'ms_accounts_combo'):
            return
        accounts = [entry for entry in self.auth.get_saved_accounts() if isinstance(entry, dict) if entry.get('type') == 'microsoft']
        previous = self.ms_accounts_combo.currentData() or {}
        previous_id = previous.get('id') if isinstance(previous, dict) else ''
        self.ms_accounts_combo.blockSignals(True)
        self.ms_accounts_combo.clear()
        self.ms_accounts_combo.setVisible(True)
        self.ms_saved_login_btn.setVisible(True)
        if self._ms_login_progress_value < 100:
            self.ms_saved_login_btn.setText('登录')
        self.ms_device_login_btn.hide()
        last_id = str(self.config.get('last_microsoft_account_id', '') or '')
        if not last_id:
            last_id = self._current_account_id()
        accounts.sort(key=(lambda entry: 0 if entry.get('id') == last_id else 1))
        for index, entry in enumerate(accounts):
            # 正版账号列表只显示名称，不再显示头像。
            self.ms_accounts_combo.addItem(entry.get('name') or '未命名', entry)
            self.ms_accounts_combo.setItemData(index, entry.get('email') or entry.get('uuid') or '', Qt.ToolTipRole)
        self.ms_accounts_combo.addItem('添加新账号', {'type': 'new'})
        select_index = self.ms_accounts_combo.count() - 1
        if previous_id:
            for index in range(self.ms_accounts_combo.count()):
                entry = self.ms_accounts_combo.itemData(index) or {}
                if isinstance(entry, dict) and entry.get('id') == previous_id:
                    select_index = index
                    break
        elif accounts:
            for index in range(self.ms_accounts_combo.count()):
                entry = self.ms_accounts_combo.itemData(index) or {}
                if isinstance(entry, dict) and entry.get('id') == last_id:
                    select_index = index
                    break
        self.ms_accounts_combo.setCurrentIndex(select_index)
        self.ms_accounts_combo.blockSignals(False)

    def _on_ms_account_icon_ready(self, account_id: str, pixmap: QPixmap):
        if not account_id or pixmap.isNull():
            return
        combo = getattr(self, 'ms_accounts_combo', None)
        if combo is None:
            return
        for index in range(combo.count()):
            entry = combo.itemData(index) or {}
            if isinstance(entry, dict) and entry.get('id') == account_id:
                combo.setItemIcon(index, self._combo_icon(pixmap))
                break

    def _on_ms_saved_login(self):
        if self._ms_saved_login_worker is not None and self._ms_saved_login_worker.isRunning():
            return
        entry = self.ms_accounts_combo.currentData() or {}
        if not entry or entry.get('type') == 'new':
            self._start_ms_device_flow()
            return
        self._begin_ms_login_progress(5, animate=True)
        self.ms_saved_login_btn.setEnabled(False)
        self.ms_device_login_btn.setEnabled(False)
        self.ms_status_label.show()
        self.ms_status_label.setText(f'正在登录正版账号：{entry.get("name", "")}...')
        self.ms_status_label.setStyleSheet('color: rgba(255,255,255,0.6); font-size:13px;')
        worker = SavedMicrosoftLoginWorker(entry)
        self._ms_saved_login_worker = worker
        worker.success.connect(self._on_ms_saved_login_success)
        worker.error.connect(self._on_ms_saved_login_error)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect((lambda w=worker: self._on_ms_saved_login_done(w)))
        worker.start()

    def _on_ms_saved_login_done(self, worker):
        if self._ms_saved_login_worker is worker:
            self._ms_saved_login_worker = None
        # 若紧接着进入设备码登录，保持按钮置灰并继续显示百分比。
        if not self._ms_login_progress_timer.isActive():
            self.ms_saved_login_btn.setEnabled(True)
        self.ms_device_login_btn.setEnabled(True)

    def _on_ms_saved_login_success(self, account: dict):
        self._on_ms_login_success(account, direct=True, notify=False)
        self._refresh_microsoft_profile_async(user_initiated=True)

    def _on_ms_saved_login_error(self, message: str, kind: str='general'):
        self._finish_ms_login_progress(False, failed=True)
        message = friendly_auth_error(message, kind)
        self.ms_status_label.show()
        self.ms_status_label.setText(message)
        self.ms_status_label.setStyleSheet('color:#ff7777; font-size:13px;')
        self._refresh_ms_accounts_list()
        expired = kind in ('expired', 'invalid_grant') or any((marker in message for marker in ('过期', '失效', '重新登录', '重新验证')))
        if expired:
            from ui.widgets.dark_dialog import DarkQuestionDialog
            dialog = DarkQuestionDialog(self, '需要重新验证', '该正版账号的登录状态已过期，需要重新验证登录后才能继续使用。', actions=[('重新验证登录', 'relogin', True), ('取消', 'cancel', False)])
            dialog.exec()
            if dialog.choice == 'relogin':
                self._start_ms_device_flow()
            return
        _show_microsoft_login_notice(self, message, kind)

    def _begin_skin_refresh_feedback(self):
        self._skin_refresh_token += 1
        self._skin_refresh_started_at = time.monotonic()
        show_toast('正在刷新皮肤...', 'info', self)
        return self._skin_refresh_token

    def _finish_skin_refresh_feedback(self, success: bool, reason: str='',
                                       token=None):
        if token is not None and token != self._skin_refresh_token:
            return
        elapsed = max(0.0, time.monotonic() - self._skin_refresh_started_at)
        delay = max(0, int(round(500 - elapsed * 1000)))
        text = '刷新皮肤成功！' if success else (
            f'刷新皮肤失败！原因:{str(reason or "未知错误")}'
        )
        kind = 'success' if success else 'error'

        def show_result():
            if token is not None and token != self._skin_refresh_token:
                return
            self._skin_refresh_token += 1
            show_toast(text, kind, self)

        QTimer.singleShot(delay, show_result)

    def _refresh_microsoft_profile_async(self, user_initiated: bool=True):
        if self._ms_profile_worker is not None and self._ms_profile_worker.isRunning():
            return
        if self.config.get('login_type', 'offline') != 'microsoft':
            return
        token = self._begin_skin_refresh_feedback() if user_initiated else None
        worker = MicrosoftProfileSkinWorker()
        self._ms_profile_worker = worker
        worker.success.connect(
            (lambda result, ui=user_initiated, t=token:
             self._on_ms_profile_skin_ready(result, ui, t))
        )
        worker.error.connect(
            (lambda msg, kind, ui=user_initiated, t=token:
             self._on_ms_profile_skin_error(msg, kind, ui, t))
        )
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect((lambda w=worker: self._on_ms_profile_worker_done(w)))
        worker.start()

    def _on_ms_profile_worker_done(self, worker):
        if self._ms_profile_worker is worker:
            self._ms_profile_worker = None

    def _on_ms_profile_skin_ready(self, result: dict, user_initiated: bool=False,
                                  refresh_token=None):
        import time as _time
        profile = result.get('profile') or {}
        skin = result.get('skin') or {}
        skin_path = result.get('skin_path') or ''
        account = self.config.get('microsoft_account', {}) or {}
        if profile.get('name'):
            account['name'] = profile.get('name')
        if profile.get('id'):
            account['uuid'] = profile.get('id')
        if skin:
            account['skin_url'] = skin.get('url', '')
            account['skin_variant'] = skin.get('variant', '')
            account['skin_updated_at'] = _time.time()
        self.config.set('microsoft_account', account)
        try:
            self.auth.save_microsoft_account(account)
        except Exception:
            pass
        name = account.get('name', '玩家')
        self.player_name_label.setText(name)
        self.login_type_label.setText('正版账号')
        if skin_path:
            self.avatar.set_skin_file(skin_path)
        else:
            self._set_microsoft_avatar_urls(account)
        self._refresh_ms_accounts_list()
        self.account_changed.emit()
        if user_initiated:
            if skin_path:
                self._finish_skin_refresh_feedback(
                    True, token=refresh_token
                )
                return
            self._finish_skin_refresh_feedback(
                False, '官方皮肤下载失败，请检查网络后重试',
                token=refresh_token,
            )
            return
        self.ms_status_label.setText('正版账号已登录')
        self.ms_status_label.setStyleSheet('color:rgba(255,255,255,0.6); font-size:13px;')

    def _on_ms_profile_skin_error(self, message: str, kind: str='general',
                                  user_initiated: bool=True, refresh_token=None):
        message = friendly_auth_error(message, kind)
        if user_initiated:
            self._finish_skin_refresh_feedback(
                False, message, token=refresh_token
            )
            return
        self.ms_status_label.setText(message)
        self.ms_status_label.setStyleSheet('color:#ff7777; font-size:13px;')

    def _refresh_thirdparty_skin_async(self, user_initiated: bool=False):
        if self.config.get('login_type', 'offline') != 'thirdparty':
            return
        if self._tp_skin_worker is not None and self._tp_skin_worker.isRunning():
            return
        import time as _time
        now = _time.time()
        if not user_initiated:
            if now - self._tp_skin_last_attempt < 30:
                return
        self._tp_skin_last_attempt = now
        token = self._begin_skin_refresh_feedback() if user_initiated else None
        worker = ThirdPartySkinWorker()
        self._tp_skin_worker = worker
        worker.success.connect(
            (lambda result, ui=user_initiated, t=token:
             self._on_tp_skin_ready(result, ui, t))
        )
        worker.error.connect(
            (lambda msg, kind, ui=user_initiated, t=token:
             self._on_tp_skin_error(msg, kind, ui, t))
        )
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect((lambda w=worker: self._on_tp_skin_worker_done(w)))
        worker.start()

    def _on_tp_skin_worker_done(self, worker):
        if self._tp_skin_worker is worker:
            self._tp_skin_worker = None

    def _on_tp_skin_ready(self, result: dict, user_initiated: bool=False,
                          refresh_token=None):
        account = self.config.get('thirdparty_account', {}) or {}
        if result.get('name'):
            account['name'] = result['name']
        if result.get('uuid'):
            account['uuid'] = result['uuid']
        if result.get('skin_url'):
            account['skin_url'] = result['skin_url']
        if result.get('model'):
            account['skin_model'] = result['model']
        if result.get('cape_url'):
            account['cape_url'] = result['cape_url']
        self.config.set('thirdparty_account', account)
        try:
            self.auth.save_thirdparty_account(account)
        except Exception:
            pass
        skin_path = result.get('skin_path') or ''
        if skin_path:
            self.avatar.set_skin_file(skin_path)
        else:
            avatar_url = account.get('avatar_url') or ''
            steve = Path(__file__).resolve().parent.parent / 'imgs' / 'steve_head.png'
            self.avatar.set_image_url([avatar_url] if avatar_url else [], fallback_head=steve)
        self.player_name_label.setText(account.get('name') or '玩家')
        self.login_type_label.setText(f'第三方登录 · {account.get("server_name", "Authlib Injector")}')
        self.account_changed.emit()
        if user_initiated:
            if skin_path:
                self._finish_skin_refresh_feedback(
                    True, token=refresh_token
                )
                return
            self._finish_skin_refresh_feedback(
                False, '皮肤下载失败，请检查网络后重试',
                token=refresh_token,
            )

    def _on_tp_skin_error(self, message: str, kind: str='general',
                          user_initiated: bool=False, refresh_token=None):
        message = friendly_auth_error(message, kind)
        if user_initiated:
            self._finish_skin_refresh_feedback(
                False, message, token=refresh_token
            )

    def _set_microsoft_avatar_urls(self, account: dict):
        from core.skin import avatar_urls_for_account
        try:
            from core.paths import scl_dir
            key = str(account.get('uuid') or account.get('name') or 'unknown')
            cache_path = scl_dir() / 'icon_cache' / f'microsoft_avatar_{key}.png'
        except Exception:
            cache_path = None
        steve = Path(__file__).resolve().parent.parent / 'imgs' / 'steve_head.png'
        self.avatar.set_image_url(
            avatar_urls_for_account(account), fallback_head=steve, cache_path=cache_path
        )

    def _on_ms_device_login(self):
        if self._ms_device_worker and self._ms_device_worker.isRunning():
            return
        if self._ms_saved_login_worker and self._ms_saved_login_worker.isRunning():
            return
        self._close_ms_code_dialog()
        self._start_ms_device_flow()

    def _start_ms_device_flow(self):
        if self._ms_device_worker and self._ms_device_worker.isRunning():
            return
        self._begin_ms_login_progress(5, animate=False)
        self.ms_device_login_btn.setEnabled(False)
        self.ms_device_code_label.hide()
        self.ms_status_label.show()
        self.ms_status_label.setText('正在获取正版登录代码...')
        self.ms_status_label.setStyleSheet('color: rgba(255,255,255,0.6); font-size:13px;')
        worker = MicrosoftDeviceLoginWorker()
        worker.code_ready.connect(self._on_ms_code_ready)
        worker.status_changed.connect(self._on_ms_device_status)
        worker.progress.connect(self._on_ms_device_progress)
        worker.success.connect(self._on_ms_device_success)
        worker.error.connect(self._on_ms_device_error)
        worker.finished.connect((lambda w=worker: self._on_ms_device_worker_done(w)))
        self._ms_device_worker = worker
        worker.start()

    def _on_ms_silent_worker_done(self, worker):
        if self._ms_silent_worker is worker:
            self._ms_silent_worker = None
        worker.deleteLater()

    def _on_ms_silent_success(self, account):
        self._on_ms_login_success(account, direct=True)

    def _on_ms_silent_error(self, message, kind='general'):
        if kind in ('expired', 'none'):
            self.ms_status_label.setText('保存的登录凭据已失效，正在获取正版登录代码...')
            self.ms_status_label.setStyleSheet('color: rgba(255,255,255,0.6); font-size:13px;')
            self._start_ms_device_flow()
            return
        self.ms_device_login_btn.setEnabled(True)
        self.ms_status_label.setText(message)
        self.ms_status_label.setStyleSheet('color:#ff7777; font-size:13px;')
        _show_microsoft_login_notice(self, message, kind)

    def _on_ms_device_worker_done(self, worker):
        if self._ms_device_worker is worker:
            self._ms_device_worker = None
        worker.deleteLater()

    def _on_ms_code_ready(self, user_code, uri):
        self._ms_device_code = user_code
        self._ms_device_uri = uri
        self.ms_device_code_label.setText(user_code)
        self.ms_device_code_label.show()
        self._set_ms_login_progress(5)
        QApplication.clipboard().setText(user_code)
        self.ms_status_label.setText('登录代码已复制，请在弹窗中点击「确定」或「复制」打开网页')
        if self._ms_code_dialog is not None:
            try:
                self._ms_code_dialog.close()
            except RuntimeError:
                pass
        self._ms_code_dialog = DeviceCodeDialog(user_code, uri, self)
        self._ms_code_dialog.show()
        self._ms_code_dialog.raise_()
        self._ms_code_dialog.activateWindow()

    def _close_ms_code_dialog(self):
        dialog = self._ms_code_dialog
        self._ms_code_dialog = None
        if dialog is not None:
            try:
                dialog.close()
            except RuntimeError:
                pass

    def _on_ms_device_status(self, text):
        # 网页授权阶段始终停在 5%；授权完成后由真实登录阶段推进进度。
        self.ms_status_label.setText(text)

    def _on_ms_device_progress(self, value):
        self._ms_login_progress_timer.stop()
        self._set_ms_login_progress(value)
        messages = {
            25: '账户已授权，正在验证 Xbox Live 账号...',
            40: '正在获取 Xbox 安全令牌...',
            55: '正在验证 Minecraft 账号...',
            70: '正在获取玩家档案...',
            85: '正在加载皮肤与账号信息...',
        }
        text = messages.get(int(value))
        if text:
            self.ms_status_label.setText(text)
            self.ms_status_label.setStyleSheet(
                'color: rgba(255,255,255,0.6); font-size:13px;'
            )

    def _on_ms_device_success(self, account):
        self.ms_device_login_btn.setEnabled(True)
        self.ms_saved_login_btn.setEnabled(True)
        self.ms_device_code_label.hide()
        self._close_ms_code_dialog()
        self._on_ms_login_success(account)
        self._refresh_microsoft_profile_async(user_initiated=False)

    def _on_ms_device_error(self, message, kind='general'):
        self._finish_ms_login_progress(False, failed=True)
        message = friendly_auth_error(message, kind)
        self.ms_device_login_btn.setEnabled(True)
        self.ms_saved_login_btn.setEnabled(True)
        self.ms_status_label.setText(message)
        self.ms_status_label.setStyleSheet('color:#ff7777; font-size:13px;')
        self.ms_device_code_label.hide()
        self._close_ms_code_dialog()
        _show_microsoft_login_notice(self, message, kind)

    def _on_ms_login_success(self, account, direct=False, notify=True):
        self._finish_ms_login_progress(True)
        if direct:
            self.ms_status_label.setText(f'已使用保存的登录凭据直接登录：{account.get("name")}')
        else:
            self.ms_status_label.setText(f'登录成功！玩家：{account.get("name")}')
        self.ms_status_label.setStyleSheet('color:#4ade80; font-size:13px;')
        self.ms_device_login_btn.setEnabled(True)
        self.ms_saved_login_btn.setEnabled(True)
        self._close_ms_code_dialog()
        self._tp_inline_switching = False
        self._ms_switching = False
        self._refresh_account_info()
        self._refresh_ms_accounts_list()
        self.account_changed.emit()
        if notify:
            QMessageBox.information(self, '登录成功', f'欢迎，{account.get("name", "玩家")}！')

    def _on_logout(self):
        self._finish_ms_login_progress(False)
        self._close_ms_code_dialog()
        # 不等待网络线程，避免退出登录卡 UI；旧线程的信号由 worker done 回调丢弃
        self._thirdparty_worker = None
        self._tp_inline_worker = None
        self.tp_login_btn.setEnabled(True)
        self.tp_login_btn.setText('第三方登录')
        self.tp_inline_login_btn.setEnabled(True)
        self.tp_inline_login_btn.setText('登录')
        self._tp_inline_switching = False
        self._ms_switching = False
        self.tp_inline_wrap.hide()
        self.auth.logout()
        self.name_input.clear()
        self._refresh_account_info()
        self._refresh_ms_accounts_list()
        self.account_changed.emit()
        QMessageBox.information(self, '已退出', '已退出当前账号，切换为离线登录')


# WARNING: Decompyle incomplete
