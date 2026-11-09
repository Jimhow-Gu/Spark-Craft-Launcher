'''统一的加载遮罩：主题圆角卡片、循环 GIF、实时百分比和失败状态。'''

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QEvent, Qt, QTimer, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtGui import QColor, QMovie, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.app_font import ui_font
from ui.widgets.toast import show_toast

_IMAGES_DIR = Path(__file__).resolve().parent.parent / 'imgs'
_LOADING_GIF = _IMAGES_DIR / 'Loading_small.gif'
if not _LOADING_GIF.is_file():
    _LOADING_GIF = _IMAGES_DIR / 'Loading.gif'


def _error_pixmap(size=64):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor('#e0443e'))
    painter.drawEllipse(2, 2, size - 4, size - 4)
    pen = QPen(QColor('#ffffff'), max(3.0, size * 0.11))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    margin = size * 0.29
    painter.drawLine(int(margin), int(margin), int(size - margin), int(size - margin))
    painter.drawLine(int(size - margin), int(margin), int(margin), int(size - margin))
    painter.end()
    return pixmap


class _SmoothIconLabel(QWidget):
    '''用 60fps 交叉淡化播放原加载图标的每一帧，避免 GIF 3.7fps 的卡顿。'''

    FRAME_MS = 270

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames = self._load_frames()
        self._error = None
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(84, 84)

    @staticmethod
    def _load_frames():
        movie = QMovie(str(_LOADING_GIF))
        movie.setCacheMode(QMovie.CacheAll)
        if not movie.isValid():
            return []
        frames = []
        movie.start()
        for index in range(max(1, movie.frameCount())):
            if not movie.jumpToFrame(index):
                continue
            pix = movie.currentPixmap()
            if not pix.isNull():
                frames.append(pix)
        movie.stop()
        return frames

    def start(self):
        self._error = None
        if self._frames and not self._timer.isActive():
            self._clock.start()
            self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()
        self._error = None
        self.update()

    def show_error(self, pixmap):
        self._timer.stop()
        self._error = pixmap
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if self._error is not None and not self._error.isNull():
            side = min(self.width(), self.height())
            pix = self._error.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(
                (self.width() - pix.width()) // 2,
                (self.height() - pix.height()) // 2,
                pix,
            )
            return
        count = len(self._frames)
        if count == 0:
            return
        position = self._clock.elapsed() / float(self.FRAME_MS)
        index = int(position) % count
        fraction = position - int(position)
        target = self.rect()
        # 两帧交叉淡化，让原图标的爆闪过渡连续，而不是 270ms 跳一帧。
        painter.setOpacity(1.0 - fraction)
        painter.drawPixmap(target, self._frames[index])
        if fraction > 0.0:
            painter.setOpacity(fraction)
            painter.drawPixmap(target, self._frames[(index + 1) % count])
        painter.end()


class LoadingOverlay(QWidget):
    '''覆盖在页面/列表上的加载卡片，支持进度、循环动画和失败状态。'''

    hidden = Signal()

    def __init__(self, parent=None, *, block_input=True):
        super().__init__(parent)
        self._host = None
        self._mode = 'idle'
        self._block_input = bool(block_input)
        self._reason = ''
        self._percent = 0
        self._auto_max = 90
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_NoMousePropagation, True)
        self.setProperty('_theme_ignore', True)
        if not self._block_input:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        middle = QHBoxLayout()
        middle.addStretch(1)
        self.card = QFrame()
        self.card.setObjectName('LoadingCard')
        self.card.setFixedSize(250, 176)
        self.card.setProperty('_theme_ignore', True)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 14, 18, 16)
        card_layout.setSpacing(7)
        self.media = QFrame()
        self.media.setObjectName('LoadingMedia')
        self.media.setFixedSize(94, 94)
        self.media.setProperty('_theme_ignore', True)
        media_layout = QVBoxLayout(self.media)
        media_layout.setContentsMargins(4, 4, 4, 4)
        media_layout.setAlignment(Qt.AlignCenter)
        self.image_label = _SmoothIconLabel()
        self.image_label.setProperty('_theme_ignore', True)
        media_layout.addWidget(self.image_label)
        card_layout.addWidget(self.media, 0, Qt.AlignHCenter)
        self.status_label = QLabel('正在加载 - 0%')
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setFont(ui_font(13, 500))
        self.status_label.setProperty('_theme_ignore', True)
        card_layout.addWidget(self.status_label)
        middle.addWidget(self.card)
        middle.addStretch(1)
        outer.addLayout(middle)
        outer.addStretch(1)

        self._shadow = QGraphicsDropShadowEffect(self.card)
        self._shadow.setBlurRadius(28)
        self._shadow.setOffset(0, 6)
        self.card.setGraphicsEffect(self._shadow)

        self._display_percent = 0.0
        self._progress_anim = QVariantAnimation(self)
        self._progress_anim.setDuration(220)
        self._progress_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._progress_anim.valueChanged.connect(self._on_progress_value)
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(140)
        self._auto_timer.timeout.connect(self._tick_auto)
        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self._show_error_toast)
        self._apply_theme()
        self.hide()

    # ---------- 挂载与尺寸 ----------
    def attach_to(self, host):
        if self._host is host:
            return
        if self._host is not None:
            try:
                self._host.removeEventFilter(self)
            except (RuntimeError, AttributeError):
                pass
        self._host = host
        self.setParent(host)
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.raise_()
        self.hide()

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() in (QEvent.Resize, QEvent.Show):
            try:
                self.setGeometry(self._host.rect())
                if event.type() == QEvent.Show and self.isVisible():
                    self.raise_()
            except RuntimeError:
                pass
        return super().eventFilter(obj, event)

    # ---------- 主题 ----------
    def set_light(self, light):
        # ThemeManager 会在切换主题时显式调用；不依赖不同平台下是否
        # 能收到 PaletteChange/StyleChange 事件。
        self._apply_theme()

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self._apply_theme()
        super().changeEvent(event)

    def _is_light(self):
        try:
            palette = QApplication.palette()
            return palette.color(QPalette.Window).lightness() > 128
        except Exception:
            return True

    def _apply_theme(self):
        light = self._is_light()
        if light:
            card_bg = '#ffffff'
            card_border = 'rgba(0,0,0,0.09)'
            text = '#1b1b1b'
            media_bg = 'transparent'
            media_border = 'transparent'
            shadow = QColor(0, 0, 0, 65)
        else:
            card_bg = '#20232b'
            card_border = 'rgba(255,255,255,0.12)'
            text = '#f0f1f3'
            media_bg = 'transparent'
            media_border = 'transparent'
            shadow = QColor(0, 0, 0, 150)
        self.card.setStyleSheet(
            f'QFrame#LoadingCard {{ background:{card_bg}; border:1px solid {card_border};'
            ' border-radius:14px; }'
            f'QFrame#LoadingCard QLabel {{ color:{text}; background:transparent; border:none; }}'
            'QFrame#LoadingMedia { background:transparent; border:none; }'
        )
        self._shadow.setColor(shadow)
        if self._mode == 'error':
            self.status_label.setStyleSheet(
                'color:#ff5a5a; background:transparent; border:none;'
            )
        else:
            self.status_label.setStyleSheet(
                f'color:{text}; background:transparent; border:none;'
            )

    # ---------- 状态 ----------
    def start(self, text='正在加载', percent=0, *, auto=False, auto_max=90):
        self._mode = 'loading'
        self._reason = ''
        self._percent = max(0, min(100, int(percent or 0)))
        self._auto_max = max(1, min(99, int(auto_max)))
        self._error_timer.stop()
        self._auto_timer.stop()
        self._progress_anim.stop()
        self._display_percent = float(self._percent)
        self.media.show()
        self.media.setStyleSheet('QFrame#LoadingMedia { background:transparent; border:none; }')
        self.image_label.start()
        self.status_label.setText(f'正在加载 - {self._percent}%')
        self._apply_theme()
        if self._host is not None:
            self.setGeometry(self._host.rect())
        self.show()
        self.raise_()
        if auto:
            self._auto_timer.start()

    def start_auto(self, text='正在加载', max_percent=90):
        self.start(text, 0, auto=True, auto_max=max_percent)

    def is_loading(self):
        return self._mode == 'loading'

    def is_error(self):
        return self._mode == 'error'

    def set_progress(self, percent):
        if self._mode != 'loading':
            return
        try:
            value = int(percent)
        except (TypeError, ValueError):
            return
        value = max(0, min(100, value))
        if value < self._percent:
            value = self._percent
        self._percent = value
        self._animate_progress(value)

    def _on_progress_value(self, value):
        try:
            self._display_percent = float(value)
        except (TypeError, ValueError):
            return
        self.status_label.setText(f'正在加载 - {int(round(self._display_percent))}%')

    def _animate_progress(self, target, duration=220):
        target = max(0, min(100, int(target)))
        if target <= self._display_percent + 0.05:
            self._display_percent = max(self._display_percent, float(target))
            self._on_progress_value(self._display_percent)
            return
        self._progress_anim.stop()
        self._progress_anim.setDuration(int(duration))
        self._progress_anim.setStartValue(float(self._display_percent))
        self._progress_anim.setEndValue(float(target))
        self._progress_anim.start()

    def _tick_auto(self):
        if self._mode != 'loading':
            self._auto_timer.stop()
            return
        if self._percent >= self._auto_max:
            self._auto_timer.stop()
            return
        remaining = self._auto_max - self._percent
        step = max(1, int(remaining * 0.08))
        self.set_progress(self._percent + step)

    def finish(self):
        if self._mode == 'error':
            return
        if self._mode == 'finishing':
            return
        self._mode = 'finishing'
        self._auto_timer.stop()
        self._error_timer.stop()
        self._percent = 100
        try:
            self.raise_()
        except RuntimeError:
            pass
        self._animate_progress(100, 220)
        QTimer.singleShot(300, self._hide_after_finish)

    def _hide_after_finish(self):
        if self._mode != 'finishing':
            return
        self._mode = 'idle'
        self._progress_anim.stop()
        self.image_label.stop()
        self._display_percent = 100.0
        self.status_label.setText('正在加载 - 100%')
        self.hide()
        self.hidden.emit()

    def fail(self, reason=''):
        self._mode = 'error'
        self._reason = str(reason or '').strip() or '请检查网络后重试'
        self._auto_timer.stop()
        self._progress_anim.stop()
        self.image_label.show_error(_error_pixmap(58))
        self.media.setStyleSheet('QFrame#LoadingMedia { background:transparent; border:none; }')
        self.status_label.setText('加载失败')
        self.status_label.setStyleSheet(
            'color:#ff5a5a; background:transparent; border:none;'
        )
        if self._host is not None:
            self.setGeometry(self._host.rect())
        self.show()
        self.raise_()
        self._error_timer.start(650)

    def _show_error_toast(self):
        if self._mode != 'error' or not self.isVisible():
            return
        show_toast(f'加载失败：{self._reason}', 'error', self._host or self.window())

    def stop(self):
        self._mode = 'idle'
        self._auto_timer.stop()
        self._error_timer.stop()
        self._progress_anim.stop()
        self.image_label.stop()
        self.hide()

    # ---------- 输入拦截 ----------
    def mousePressEvent(self, event):
        if self._block_input:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._block_input:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._block_input:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        if self._block_input:
            event.accept()
            return
        super().wheelEvent(event)
