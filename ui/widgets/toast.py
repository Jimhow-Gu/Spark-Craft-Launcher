'''底部滑入式通知条：普通黑色、成功绿色、失败红色。'''

from __future__ import annotations
from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRect, QRectF, QThread, QTimer, QVariantAnimation, Qt, Signal)
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget)
from core.app_font import ui_font
_LEVEL_STYLE = {'info': {'background': 'rgba(18,19,22,246)', 'border': 'rgba(255,255,255,0.20)', 'accent': '#e8e8e8', 'mark': 'i', 'duration': 4200, 'text': '#f3f4f6', 'close': 'rgba(255,255,255,0.70)', 'close_hover': '#ffffff', 'mark_text': '#111111', 'track': (255, 255, 255, 30), 'shadow': 160}, 'warning': {'background': 'rgba(18,19,22,246)', 'border': 'rgba(251,191,36,0.48)', 'accent': '#fbbf24', 'mark': '!', 'duration': 5000, 'text': '#f3f4f6', 'close': 'rgba(255,255,255,0.70)', 'close_hover': '#ffffff', 'mark_text': '#111111', 'track': (255, 255, 255, 30), 'shadow': 160}, 'success': {'background': 'rgba(18,66,40,246)', 'border': 'rgba(74,222,128,0.58)', 'accent': '#4ade80', 'mark': '✓', 'duration': 3600, 'text': '#f3f4f6', 'close': 'rgba(255,255,255,0.70)', 'close_hover': '#ffffff', 'mark_text': '#111111', 'track': (255, 255, 255, 30), 'shadow': 160}, 'error': {'background': 'rgba(84,24,26,246)', 'border': 'rgba(248,113,113,0.62)', 'accent': '#f87171', 'mark': '×', 'duration': 5600, 'text': '#f3f4f6', 'close': 'rgba(255,255,255,0.70)', 'close_hover': '#ffffff', 'mark_text': '#111111', 'track': (255, 255, 255, 30), 'shadow': 160}}
_LIGHT_LEVEL_STYLE = {'info': {'background': 'rgba(255,255,255,250)', 'border': 'rgba(0,0,0,0.14)', 'accent': '#0078d4', 'mark': 'i', 'duration': 4200, 'text': '#1b1b1b', 'close': 'rgba(0,0,0,0.55)', 'close_hover': '#1b1b1b', 'mark_text': '#ffffff', 'track': (0, 0, 0, 26), 'shadow': 55}, 'warning': {'background': 'rgba(255,248,232,250)', 'border': 'rgba(217,142,4,0.48)', 'accent': '#d98e04', 'mark': '!', 'duration': 5000, 'text': '#4d3a0a', 'close': 'rgba(0,0,0,0.55)', 'close_hover': '#1b1b1b', 'mark_text': '#ffffff', 'track': (0, 0, 0, 26), 'shadow': 55}, 'success': {'background': 'rgba(237,249,240,250)', 'border': 'rgba(46,158,91,0.45)', 'accent': '#2e9e5b', 'mark': '✓', 'duration': 3600, 'text': '#14532d', 'close': 'rgba(0,0,0,0.55)', 'close_hover': '#1b1b1b', 'mark_text': '#ffffff', 'track': (0, 0, 0, 26), 'shadow': 55}, 'error': {'background': 'rgba(253,238,238,250)', 'border': 'rgba(209,52,56,0.42)', 'accent': '#d13438', 'mark': '×', 'duration': 5600, 'text': '#7a1f1f', 'close': 'rgba(0,0,0,0.55)', 'close_hover': '#1b1b1b', 'mark_text': '#ffffff', 'track': (0, 0, 0, 26), 'shadow': 55}}


for _styles in (_LEVEL_STYLE, _LIGHT_LEVEL_STYLE):
    _update_style = dict(_styles['success'])
    _update_style['mark'] = '↑'
    _update_style['duration'] = 15000
    _styles['update'] = _update_style

def _compact_text(text, limit=240):
    value = ' '.join(str(text or '').split())
    if len(value) > limit:
        value = value[:limit - 1].rstrip() + '…'
    return value

class _ToastProgress(QWidget):
    def __init__(self, accent, parent=None, track=None):
        super().__init__(parent)
        self._ratio = 1.0
        self._accent = QColor(accent)
        self._track = QColor(*track) if isinstance(track, tuple) else QColor(255, 255, 255, 30)
        self.setFixedHeight(4)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_ratio(self, value):
        try:
            self._ratio = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._ratio = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        bar = QRectF(10, 0, max(0.0, self.width() - 20), 3.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._track)
        painter.drawRoundedRect(bar, 1.75, 1.75)
        if self._ratio <= 0:
            return
        fill = QRectF(bar.left(), bar.top(), bar.width() * self._ratio, bar.height())
        painter.setBrush(self._accent)
        painter.drawRoundedRect(fill, 1.75, 1.75)


class Toast(QFrame):
    '''单个通知条，滑入后在进度条耗尽时沿原路滑出。'''

    def __init__(self, parent, text, level='info', manager=None, on_click=None):
        super().__init__(parent)
        self._manager = manager
        self._on_click = on_click if callable(on_click) else None
        self._level = level if level in _LEVEL_STYLE else 'info'
        palette = QApplication.palette()
        light = palette.color(QPalette.Window).lightness() > 128
        style = (_LIGHT_LEVEL_STYLE if light else _LEVEL_STYLE)[self._level]
        self.setProperty('_theme_ignore', True)
        self._dismissing = False
        self._closed = False
        self._hovered = False
        self._target = QPoint(0, 0)
        self.setObjectName('ToastCard')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        if self._on_click is not None:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(str(text or '').strip() + '\n点击执行操作')
        self.setStyleSheet(f'QFrame#ToastCard {{ background:{style["background"]}; border:1px solid {style["border"]}; border-radius:11px; }} QLabel {{ background:transparent; }}')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 3)
        outer.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(15, 11, 9, 9)
        row.setSpacing(10)
        icon = QLabel(style['mark'])
        icon.setFixedSize(24, 24)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f'background:{style["accent"]}; color:{style["mark_text"]}; border-radius:12px; font-size:15px; font-weight:800;')
        row.addWidget(icon, 0, Qt.AlignTop)
        full_text = str(text or '').strip()
        label = QLabel(_compact_text(full_text))
        label.setWordWrap(True)
        label.setFont(ui_font(12))
        label.setStyleSheet(f'color:{style["text"]}; font-size:13px;')
        label.setToolTip(full_text)
        label.setMinimumWidth(180)
        row.addWidget(label, 1)
        close = QPushButton('✕')
        close.setFixedSize(26, 24)
        close.setToolTip('关闭通知')
        close.setFocusPolicy(Qt.NoFocus)
        close.setStyleSheet(
            f'QPushButton {{ background:transparent; color:{style["close"]};'
            ' border:none; border-radius:5px; font-size:12px; }'
            f'QPushButton:hover {{ background:rgba(0,0,0,0.08); color:{style["close_hover"]}; }}'
        )
        close.clicked.connect(self.dismiss)
        row.addWidget(close, 0, Qt.AlignTop)
        outer.addLayout(row)
        self.progress = _ToastProgress(style['accent'], self, style.get('track'))
        outer.addWidget(self.progress)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, int(style.get('shadow', 160))))
        self.setGraphicsEffect(shadow)
        max_width = 580
        if parent is not None:
            max_width = max(300, min(max_width, parent.width() - 100))
        self.setFixedWidth(max_width)
        text_width = max(170, max_width - 100)
        text_rect = label.fontMetrics().boundingRect(QRect(0, 0, text_width, 1000), Qt.AlignLeft | Qt.TextWordWrap, label.text())
        label.setFixedHeight(min(54, max(18, text_rect.height())))
        self.adjustSize()
        self.setFixedHeight(max(58, min(92, self.sizeHint().height())))
        for child in self.findChildren(QWidget):
            child.setProperty('_theme_ignore', True)
        self._slide = QPropertyAnimation(self, b'pos', self)
        self._slide.setDuration(260)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._slide.finished.connect(self._on_slide_finished)
        self._progress_anim = QVariantAnimation(self)
        self._progress_anim.setStartValue(1.0)
        self._progress_anim.setEndValue(0.0)
        self._progress_anim.setDuration(int(style['duration']))
        self._progress_anim.setEasingCurve(QEasingCurve.Linear)
        self._progress_anim.valueChanged.connect(self.progress.set_ratio)
        self._progress_anim.finished.connect(self.dismiss)
        if parent is not None:
            parent.installEventFilter(self)

    def show_at(self, target, animate=True):
        if self._dismissing or self._closed:
            return
        self._target = QPoint(target)
        parent = self.parentWidget()
        if not animate:
            self.move(self._target)
            self.show()
            self.raise_()
            self._progress_anim.start()
            return
        start = QPoint(self._target.x(), (parent.height() if parent else 0) + 6)
        self.move(start)
        self.show()
        self.raise_()
        self._slide.stop()
        self._slide.setStartValue(start)
        self._slide.setEndValue(self._target)
        self._slide.setDuration(270)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._slide.start()
        self._progress_anim.start()

    def _move_to(self, target):
        if self._dismissing or self._closed:
            return
        self._target = QPoint(target)
        if not self.isVisible():
            self.move(self._target)
            return
        if self.pos() == self._target:
            return
        self._slide.stop()
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(self._target)
        self._slide.setDuration(180)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._slide.start()

    def dismiss(self):
        if self._dismissing or self._closed:
            return
        self._dismissing = True
        self._progress_anim.stop()
        self._slide.stop()
        if self._manager is not None:
            self._manager._toast_closing(self)
        parent = self.parentWidget()
        end_y = (parent.height() if parent else self.y() + self.height()) + 8
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(QPoint(self._target.x(), end_y))
        self._slide.setDuration(220)
        self._slide.setEasingCurve(QEasingCurve.InCubic)
        self._slide.start()

    def finish_now(self):
        self._closed = True
        self._progress_anim.stop()
        self._slide.stop()
        if self._manager is not None:
            self._manager._toast_finished(self)
            return
        self.deleteLater()

    def _on_slide_finished(self):
        if self._dismissing:
            self.finish_now()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_click is not None and not self._dismissing:
            action = self._on_click
            self._on_click = None
            self.dismiss()
            QTimer.singleShot(0, action)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        if not self._dismissing:
            self._progress_anim.pause()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if not self._dismissing:
            self._progress_anim.resume()
        super().leaveEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize and self._manager is not None:
            self._manager._relayout(self.parentWidget())
        return super().eventFilter(watched, event)


class ToastManager(QObject):
    _instance = None
    _queue_request = Signal(str, str, object, object, object)
    def __init__(self):
        super().__init__()
        self._toasts = []
        self._queue_request.connect(self._show_from_gui_thread, Qt.QueuedConnection)

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = ToastManager()
        return cls._instance

    def _show_from_gui_thread(self, text, level, parent, duration, on_click):
        self.show(text, level, parent, duration, on_click)

    @staticmethod
    def _window_for(parent):
        if parent is not None:
            window = parent.window() if hasattr(parent, 'window') else parent
            if window is not None and window.isVisible():
                return window
        app = QApplication.instance()
        if app is None:
            return
        active = app.activeWindow()
        if active is not None and active.isVisible():
            return active
        for widget in app.topLevelWidgets():
            if isinstance(widget, QMainWindow) and widget.isVisible():
                return widget

    def show(self, text, level='info', parent=None, duration=None, on_click=None):
        app = QApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            self._queue_request.emit(str(text or ''), str(level or 'info'), parent, duration, on_click)
            return True
        window = self._window_for(parent)
        if window is not None:
            if not str(text or '').strip():
                return False
        toast = Toast(window, text, level, self, on_click)
        if duration is not None:
            toast._progress_anim.setDuration(max(1200, int(duration)))
        active = [t for t in self._toasts if t.parentWidget() is window if not t._closed]
        while len(active) >= 4:
            oldest = active.pop(0)
            oldest.finish_now()
        self._toasts.append(toast)
        self._relayout(window, new_toast=toast)
        return True

    def _relayout(self, window, new_toast=None):
        if window is None:
            return
        active = [t for t in self._toasts if t.parentWidget() is window if not t._dismissing if not t._closed]
        bottom = window.height() - 26
        for toast in reversed(active):
            x = max(12, (window.width() - toast.width()) // 2)
            y = bottom - toast.height()
            bottom = y - 8
            target = QPoint(x, y)
            if toast is new_toast:
                toast.show_at(target, animate=True)
            else:
                toast._move_to(target)

    def _toast_closing(self, toast):
        window = toast.parentWidget()
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._relayout(window)

    def _toast_finished(self, toast):
        if toast in self._toasts:
            self._toasts.remove(toast)
        toast.deleteLater()

    def clear(self):
        for toast in list(self._toasts):
            toast.finish_now()
        self._toasts.clear()


def show_toast(text, level='info', parent=None, duration=None, on_click=None):
    '''在主窗口底部显示通知条；没有可用窗口时返回 False。'''

    return ToastManager.instance().show(text, level, parent, duration, on_click)

def notify_network_failure(parent=None, action='下载文件', guidance=''):
    '''统一网络失败提示：先提示失败，再给出 VPN/加速器建议。'''

    action = str(action or '下载文件')
    show_toast(f'因为网络问题导致{action}失败！', 'error', parent)
    text = guidance or '当前网络问题导致无法下载，请使用加速器/VPN后重试！'
    QTimer.singleShot(260, (lambda: show_toast(text, 'error', parent)))

def toast_severity_for_title(title, level='info', text=''):
    title = str(title or '')
    if level == 'error':
        return 'error'
    if level == 'warning':
        return 'warning'
    blob = title + ' ' + str(text or '')[:120]
    if any((word in blob for word in ('失败', '错误', '无法', '异常', '超时'))):
        return 'error'
    if any((word in title for word in ('成功', '完成', '已删除', '已保存', '已添加', '已导入', '已切换', '已更新', '已清空'))):
        return 'success'
    return 'info'

