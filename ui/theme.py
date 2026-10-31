"""主题管理：深色 / 浅色 / 跟随系统，以及圆形切换动画。"""
from __future__ import annotations

import re
import time

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QDialog, QMainWindow, QMenu, QWidget,
)

from ui.widgets.checkbox_style import CheckBoxProxyStyle
from ui.widgets.theme_reveal import ThemeRevealOverlay

_THEMES = ('dark', 'light', 'system')

_MENU_LIGHT = """
QMenu { background:#fbfbfb; color:#1b1b1b; border:1px solid #c8ccd2;
        border-radius:8px; padding:5px; }
QMenu::item { padding:6px 26px 6px 18px; border-radius:5px; }
QMenu::item:selected { background:#0078d4; color:white; }
QMenu::separator { height:1px; background:rgba(0,0,0,0.12); margin:4px 8px; }
"""
_MENU_DARK = """
QMenu { background:#20232b; color:#f0f0f0; border:1px solid rgba(255,255,255,0.18);
        border-radius:8px; padding:5px; }
QMenu::item { padding:6px 26px 6px 18px; border-radius:5px; }
QMenu::item:selected { background:#0078d4; color:white; }
QMenu::separator { height:1px; background:rgba(255,255,255,0.12); margin:4px 8px; }
"""

_COLOR_TOKEN = re.compile(
    r'#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|'
    r'rgba?\([^)]*\)|\bwhite\b|\bblack\b',
    re.I,
)
_DECL = re.compile(
    r'(?P<lead>\A\s*|[;{}]\s*)(?P<prop>[A-Za-z-]+)\s*:\s*(?P<value>[^;{}]*)'
)


def _parse_color(token: str):
    text = token.strip().lower()
    if text == 'white':
        return 255, 255, 255, 255
    if text == 'black':
        return 0, 0, 0, 255
    if text.startswith('#'):
        value = text[1:]
        if len(value) in (3, 4):
            value = ''.join(ch * 2 for ch in value)
        try:
            if len(value) == 6:
                return (
                    int(value[0:2], 16),
                    int(value[2:4], 16),
                    int(value[4:6], 16),
                    255,
                )
            if len(value) == 8:
                return (
                    int(value[0:2], 16),
                    int(value[2:4], 16),
                    int(value[4:6], 16),
                    int(value[6:8], 16),
                )
        except ValueError:
            return None
    match = re.match(r'rgba?\(([^)]*)\)', text)
    if match:
        parts = [p.strip() for p in match.group(1).split(',')]
        if len(parts) >= 3:
            try:
                channels = []
                for part in parts[:3]:
                    if part.endswith('%'):
                        channels.append(int(round(float(part[:-1]) * 2.55)))
                    else:
                        channels.append(int(round(float(part))))
                alpha = 255.0
                if len(parts) > 3:
                    raw = parts[3]
                    alpha = (
                        float(raw[:-1]) * 2.55
                        if raw.endswith('%')
                        else float(raw)
                    )
                    if alpha <= 1.0:
                        alpha *= 255.0
                return (*channels, int(max(0, min(255, alpha))))
            except ValueError:
                return None
    return None


def _format_color(r: int, g: int, b: int, a: int) -> str:
    r, g, b = (max(0, min(255, int(v))) for v in (r, g, b))
    a = max(0, min(255, int(a)))
    if a >= 250:
        return '#%02x%02x%02x' % (r, g, b)
    return 'rgba(%d,%d,%d,%.2f)' % (r, g, b, a / 255.0)


def _block_has_accent_background(block: str) -> bool:
    for match in _DECL.finditer(block):
        prop = match.group('prop').lower()
        if not (prop.startswith('background') or prop.startswith('selection-background')):
            continue
        for token in _COLOR_TOKEN.finditer(match.group('value')):
            parsed = _parse_color(token.group(0))
            if parsed is None:
                continue
            r, g, b, alpha = parsed
            saturation = max(r, g, b) - min(r, g, b)
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            if saturation >= 30 and 30 <= luminance <= 225 and alpha >= 150:
                return True
    return False


def _transform_light_color(token: str, prop: str, accent_background: bool) -> str:
    parsed = _parse_color(token)
    if parsed is None:
        return token
    r, g, b, a = parsed
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    gray = max(r, g, b) - min(r, g, b) <= 26
    is_text = prop in ('color', 'selection-color', 'outline-color')
    is_border = 'border' in prop or prop in ('gridline-color',)
    if is_text:
        if accent_background and luminance >= 120:
            return token
        if gray:
            if luminance >= 200:
                return _format_color(32, 33, 36, a)
            if luminance >= 140:
                return _format_color(60, 64, 67, a)
            return token
        if luminance >= 150:
            factor = 0.6 if luminance > 210 else 0.72
            return _format_color(r * factor, g * factor, b * factor, a)
        return token
    if is_border:
        if accent_background and luminance >= 128:
            return token
        if gray:
            if a < 250:
                if luminance >= 115:
                    alpha = max(10, min(64, int(a * 0.62)))
                    return _format_color(0, 0, 0, alpha)
                return token
            if luminance < 110 or luminance >= 215:
                return _format_color(210, 214, 222, 255)
            return token
        if luminance >= 150:
            factor = 0.66 if luminance > 210 else 0.78
            return _format_color(r * factor, g * factor, b * factor, a)
        return token
    # background / background-color / gradient stops
    if accent_background:
        return token
    if not gray:
        return token
    if a < 250:
        if luminance >= 128:
            alpha = max(5, min(48, int(a * 0.7)))
            return _format_color(0, 0, 0, alpha)
        alpha = max(90, min(240, int(a + 72)))
        return _format_color(248, 249, 250, alpha)
    if luminance < 40:
        return '#fbfbfb'
    if luminance < 150:
        value = max(200, min(236, int(255 - luminance * 0.45)))
        return '#%02x%02x%02x' % (value, value, value)
    return token


def _light_qss(qss: str) -> str:
    if not qss:
        return qss
    text = str(qss)
    output = []
    cursor = 0
    for match in _DECL.finditer(text):
        prop = match.group('prop').lower()
        if prop not in (
            'color',
            'selection-color',
            'outline-color',
            'background',
            'background-color',
            'alternate-background-color',
            'selection-background-color',
            'border',
            'border-color',
            'border-top',
            'border-right',
            'border-bottom',
            'border-left',
            'border-top-color',
            'border-right-color',
            'border-bottom-color',
            'border-left-color',
            'gridline-color',
        ):
            continue
        # 用属性本身的位置查找所属规则，避免把上一条规则的强调色
        # 误当成当前规则的背景，导致按钮背景保持深色而文字变深。
        open_brace = text.rfind('{', 0, match.start('prop'))
        close_brace = text.find('}', match.start('prop'))
        block = text[open_brace + 1:close_brace if close_brace >= 0 else len(text)]
        accent_background = _block_has_accent_background(block)
        value = match.group('value')
        transformed = _COLOR_TOKEN.sub(
            lambda token: _transform_light_color(
                token.group(0), prop, accent_background
            ),
            value,
        )
        if transformed == value:
            continue
        output.append(text[cursor:match.start('value')])
        output.append(transformed)
        cursor = match.end('value')
    if not output:
        return text
    output.append(text[cursor:])
    return ''.join(output)


def _system_is_light() -> bool:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
        ) as key:
            value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            return bool(value)
    except Exception:
        palette = QApplication.palette()
        return palette.color(QPalette.Window).lightness() > 128


class ThemeManager(QObject):
    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self._window = window
        self._theme = ''
        self._light = False
        self._overlays = []
        self._pending_widgets = []
        self._pending_index = 0
        self._pending_light = False
        self._pending_generation = 0
        self._apply_generation = 0
        self._theme_updating = False
        self._app = QApplication.instance()
        self._app.installEventFilter(self)
        self._chunk_timer = QTimer(self)
        self._chunk_timer.setSingleShot(True)
        self._chunk_timer.timeout.connect(self._process_theme_chunk)
        self._system_timer = QTimer(self)
        self._system_timer.setInterval(2500)
        self._system_timer.timeout.connect(self._check_system_theme)
        self._system_timer.start()

    # ---------- 对外接口 ----------
    def apply(self, theme: str, origin_global=None, animate: bool = True):
        theme = str(theme or 'system').lower()
        if theme not in _THEMES:
            theme = 'system'
        light = theme == 'light' or (theme == 'system' and _system_is_light())
        if self._theme and light == self._light:
            self._theme = theme
            return
        if light == self._light and theme == self._theme:
            return
        if not self._window.isVisible():
            # 启动阶段窗口还没显示：只应用全局调色板，具体控件等到
            # showEvent 后一次性处理可见区域，避免启动时重排上千个控件。
            self._light = bool(light)
            self._apply_global(light, visible_only=True)
            self._theme = theme
            return
        if not animate:
            self._apply_now(light, defer_hidden=True)
            self._theme = theme
            return

        self._clear_overlays()
        windows = [
            w for w in QApplication.topLevelWidgets()
            if isinstance(w, (QMainWindow, QDialog))
            and w.isVisible() and w.width() > 0 and w.height() > 0
        ]
        if self._window.isVisible() and self._window not in windows:
            windows.insert(0, self._window)
        captures = {}
        for w in windows:
            try:
                captures[w] = w.grab()
            except RuntimeError:
                continue
        if origin_global is None:
            origin_global = QCursor.pos()
        prepared = []
        for w, pixmap in captures.items():
            if pixmap.isNull():
                continue
            center = w.mapFromGlobal(origin_global)
            if not w.rect().contains(center):
                center = w.rect().center()
            try:
                overlay = ThemeRevealOverlay(w, pixmap, center)
                overlay.done.connect(
                    lambda o=overlay: self._discard_overlay(o)
                )
                overlay.show()
                overlay.raise_()
                overlay.repaint()
                prepared.append(overlay)
            except RuntimeError:
                continue
        try:
            self._apply_now(light, defer_hidden=True)
        except Exception:
            self._clear_overlays()
            for overlay in prepared:
                try:
                    overlay.hide()
                    overlay.deleteLater()
                except RuntimeError:
                    pass
            raise
        self._theme = theme
        for overlay in prepared:
            try:
                overlay.start()
                self._overlays.append(overlay)
            except RuntimeError:
                continue

    # ---------- 应用 ----------
    def style_visible_widgets(self):
        '''窗口首次显示后补应用当前主题，隐藏页面仍按需在显示时处理。'''

        if not self._theme:
            return
        self._apply_now(self._light, defer_hidden=True)

    def style_widget_tree(self, widget):
        '''页面首次显示时补应用主题（隐藏页面不会在切换主题时被遍历）。'''

        if not self._theme or widget is None:
            return
        try:
            self._apply_tree(widget)
        except RuntimeError:
            pass

    def _apply_global(self, light: bool, visible_only: bool = False):
        self._light = bool(light)
        app = self._app
        self._theme_updating = True
        try:
            app.setPalette(self._make_palette(light))
        finally:
            self._theme_updating = False
        CheckBoxProxyStyle.set_light_mode(light)
        try:
            from ui.widgets.painted_icons import set_light_mode
            set_light_mode(light)
        except Exception:
            pass
        for widget in app.allWidgets():
            if visible_only and not widget.isVisible():
                continue
            setter = getattr(widget, 'set_light', None)
            if callable(setter):
                try:
                    setter(light)
                except Exception:
                    pass
        try:
            from ui.widgets.scrollbar_style import ScrollBarStyler
            ScrollBarStyler.set_light_mode(light, visible_only=visible_only)
        except Exception:
            pass

    def _apply_now(self, light: bool, defer_hidden: bool = False):
        self._cancel_theme_chunks()
        self._apply_global(light, visible_only=defer_hidden)
        widgets = [
            w for w in self._app.allWidgets()
            if not (defer_hidden and not w.isVisible())
        ]
        # 控件很多时分片应用，避免一次性阻塞界面（切换主题不再明显卡顿）
        if len(widgets) > 200:
            self._schedule_theme_chunks(light, widgets)
            return
        for widget in widgets:
            try:
                self._style_widget(widget, light)
                if isinstance(widget, QAbstractButton):
                    self._refresh_button_icon(widget, light)
            except RuntimeError:
                pass
        self._update_toplevels()

    def _cancel_theme_chunks(self):
        self._chunk_timer.stop()
        self._pending_widgets = []
        self._pending_index = 0
        self._apply_generation += 1

    def _schedule_theme_chunks(self, light: bool, widgets=None):
        self._cancel_theme_chunks()
        self._pending_light = bool(light)
        self._pending_generation = self._apply_generation
        if widgets is not None:
            self._pending_widgets = list(widgets)
        else:
            self._pending_widgets = [
                widget for widget in self._app.allWidgets() if widget.isVisible()
            ]
        self._pending_index = 0
        self._chunk_timer.start(0)

    def _process_theme_chunk(self):
        if self._pending_generation != self._apply_generation:
            return
        widgets = self._pending_widgets
        if not widgets:
            return
        deadline = time.perf_counter() + 0.006
        index = self._pending_index
        light = self._pending_light
        while index < len(widgets) and time.perf_counter() < deadline:
            widget = widgets[index]
            index += 1
            try:
                if widget.property('_theme_ignore'):
                    continue
                self._style_widget(widget, light)
                if isinstance(widget, QAbstractButton):
                    self._refresh_button_icon(widget, light)
            except RuntimeError:
                continue
        self._pending_index = index
        if index < len(widgets):
            self._chunk_timer.start(0)
        else:
            self._pending_widgets = []
            self._pending_index = 0
            self._update_toplevels()

    def _update_toplevels(self):
        for w in self._app.topLevelWidgets():
            try:
                w.update()
            except RuntimeError:
                pass

    def _make_palette(self, light: bool) -> QPalette:
        palette = QPalette()
        if light:
            colors = {
                QPalette.Window: '#f3f3f3',
                QPalette.WindowText: '#1b1b1b',
                QPalette.Base: '#fbfbfb',
                QPalette.AlternateBase: '#f0f0f0',
                QPalette.Text: '#1b1b1b',
                QPalette.Button: '#ececec',
                QPalette.ButtonText: '#1b1b1b',
                QPalette.ToolTipBase: '#fbfbfb',
                QPalette.ToolTipText: '#1b1b1b',
                QPalette.PlaceholderText: '#7a7f87',
            }
        else:
            colors = {
                QPalette.Window: '#121419',
                QPalette.WindowText: '#f0f0f0',
                QPalette.Base: '#181a20',
                QPalette.AlternateBase: '#20232b',
                QPalette.Text: '#f0f0f0',
                QPalette.Button: '#2a2a2a',
                QPalette.ButtonText: '#f0f0f0',
                QPalette.ToolTipBase: '#20232b',
                QPalette.ToolTipText: '#f0f0f0',
                QPalette.PlaceholderText: '#8a8f99',
            }
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        palette.setColor(QPalette.Highlight, QColor('#0078d4'))
        palette.setColor(QPalette.HighlightedText, QColor('#ffffff'))
        palette.setColor(QPalette.Disabled, QPalette.Text, QColor('#8a8f99'))
        palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor('#8a8f99'))
        return palette

    def _style_widget(self, widget: QWidget, light: bool):
        try:
            if widget.property('_theme_ignore'):
                return
            current = widget.styleSheet()
            original = getattr(widget, '_theme_original_style', None)
            cached_light = getattr(widget, '_theme_light_style', None)
            if original is None:
                if not current:
                    return
                original = current
                widget._theme_original_style = current
            elif current != original and current != cached_light:
                original = current
                widget._theme_original_style = current
                widget._theme_light_style = None
                cached_light = None
            if light:
                target = cached_light
                if target is None:
                    target = _light_qss(original)
                    widget._theme_light_style = target
            else:
                target = original
            final = target
            if widget.styleSheet() != final:
                self._theme_updating = True
                try:
                    widget.setStyleSheet(final)
                finally:
                    self._theme_updating = False
        except RuntimeError:
            pass

    def _refresh_button_icon(self, button: QAbstractButton, light: bool):
        try:
            from ui.widgets.painted_icons import refresh_button_icon
            if refresh_button_icon(button):
                return
        except Exception:
            pass
        self._style_button_icon(button, light)

    def _style_button_icon(self, button: QAbstractButton, light: bool):
        try:
            icon = button.icon()
            if icon.isNull():
                return
            if not hasattr(button, '_theme_original_icon'):
                button._theme_original_icon = QIcon(icon)
            target = (
                self._light_icon(button._theme_original_icon, button)
                if light else button._theme_original_icon
            )
            button.setIcon(target)
        except RuntimeError:
            pass

    @staticmethod
    def _light_icon(icon: QIcon, button: QAbstractButton) -> QIcon:
        size = button.iconSize()
        if size.isEmpty():
            size = icon.availableSizes()[0] if icon.availableSizes() else None
        pixmap = icon.pixmap(size) if size is not None else icon.pixmap(24, 24)
        if pixmap.isNull():
            return icon
        image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() > 0 and color.lightness() > 145:
                    if max(color.red(), color.green(), color.blue()) - min(
                        color.red(), color.green(), color.blue()
                    ) > 28:
                        color.setRgb(
                            int(color.red() * 0.62),
                            int(color.green() * 0.62),
                            int(color.blue() * 0.62),
                            color.alpha(),
                        )
                    else:
                        color.setRgb(45, 50, 62, color.alpha())
                    image.setPixelColor(x, y, color)
        return QIcon(QPixmap.fromImage(image))

    def _apply_tree(self, root: QWidget):
        widgets = [root] + [
            w for w in root.findChildren(QWidget)
        ]
        for widget in widgets:
            setter = getattr(widget, 'set_light', None)
            if callable(setter):
                try:
                    setter(self._light)
                except Exception:
                    pass
            self._style_widget(widget, self._light)
            if isinstance(widget, QAbstractButton):
                self._refresh_button_icon(widget, self._light)

    def _style_shown_widget(self, widget: QWidget):
        if isinstance(widget, QMenu):
            self._prepare_menu(widget)
            return
        if self._light and isinstance(widget, QDialog):
            # 浅色主题下新建的弹窗需要连同子控件一起转换，
            # 否则窗口显示后内部仍是深色文字/背景。
            self._apply_tree(widget)
            return
        setter = getattr(widget, 'set_light', None)
        if callable(setter):
            try:
                setter(self._light)
            except Exception:
                pass
        self._style_widget(widget, self._light)
        if isinstance(widget, QAbstractButton):
            self._refresh_button_icon(widget, self._light)

    def _prepare_menu(self, menu: QMenu):
        try:
            if getattr(menu, '_theme_default_menu', False):
                menu.setStyleSheet(_MENU_LIGHT if self._light else _MENU_DARK)
                return
            if not menu.styleSheet():
                menu._theme_default_menu = True
                menu.setStyleSheet(_MENU_LIGHT if self._light else _MENU_DARK)
                return
        except RuntimeError:
            return
        self._style_widget(menu, self._light)

    # ---------- 系统主题 / 动画清理 ----------
    def _check_system_theme(self):
        if self._theme != 'system' or not self._window.isVisible():
            return
        light = _system_is_light()
        if light != self._light:
            self.apply('system', self._window.mapToGlobal(self._window.rect().center()))

    def _clear_overlays(self):
        overlays, self._overlays = self._overlays, []
        for overlay in overlays:
            try:
                overlay.hide()
            except RuntimeError:
                pass
            try:
                overlay.deleteLater()
            except RuntimeError:
                pass

    def _discard_overlay(self, overlay):
        self._overlays = [item for item in self._overlays if item is not overlay]

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.Show
            and self._theme
            and isinstance(obj, QWidget)
        ):
            self._style_shown_widget(obj)
        elif (
            event.type() == QEvent.StyleChange
            and self._light
            and self._theme
            and not self._theme_updating
            and isinstance(obj, QWidget)
            and not (isinstance(obj, QMenu) and getattr(obj, '_theme_default_menu', False))
        ):
            try:
                self._style_widget(obj, True)
            except RuntimeError:
                pass
        return super().eventFilter(obj, event)
