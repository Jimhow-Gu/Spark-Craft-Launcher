'''主题化提示框：替代原生 QToolTip，避免控件样式表影响提示框背景。'''

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from core.app_font import ui_font


class ThemedToolTip(QLabel):
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = ThemedToolTip()
        return cls._instance

    def __init__(self):
        super().__init__(
            None,
            Qt.ToolTip
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setTextFormat(Qt.AutoText)
        self.setFont(ui_font(9))
        self.setContentsMargins(8, 5, 8, 5)
        self.setMaximumWidth(620)
        self._light = None
        self._background = QColor('#20232b')
        self._foreground = QColor('#f0f0f0')
        self._border = QColor('#3a4050')
        self.hide()

    def _apply_style(self, light: bool):
        if light == self._light:
            return
        self._light = bool(light)
        if light:
            background, foreground, border = '#fbfbfb', '#1b1b1b', '#c8ccd2'
        else:
            background, foreground, border = '#20232b', '#f0f0f0', '#3a4050'
        self._background = QColor(background)
        self._foreground = QColor(foreground)
        self._border = QColor(border)
        self.setStyleSheet(
            f'QLabel {{ background:transparent; color:{foreground}; }}'
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(self._background)
        painter.setPen(QPen(self._border, 1.0))
        painter.drawRoundedRect(rect, 6.0, 6.0)
        painter.end()
        super().paintEvent(event)

    def show_text(self, text: str, global_pos: QPoint | None = None):
        text = str(text or '')
        if not text:
            self.hide()
            return
        app = QApplication.instance()
        palette = app.palette() if app is not None else self.palette()
        self._apply_style(
            palette.color(QPalette.Window).lightness() > 128
        )
        self.setMinimumWidth(0)
        self.setMaximumWidth(620)
        self.setWordWrap(False)
        self.setText(text)
        self.adjustSize()
        if self.width() > 620:
            self.setWordWrap(True)
            self.setFixedWidth(600)
            self.adjustSize()
        pos = QPoint(global_pos) if global_pos is not None else QCursor.pos()
        screen = QApplication.screenAt(pos)
        if screen is None:
            screen = QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        x = pos.x() + 14
        y = pos.y() + 18
        if x + self.width() > bounds.right() - 4:
            x = bounds.right() - self.width() - 4
        if y + self.height() > bounds.bottom() - 4:
            y = pos.y() - self.height() - 10
        x = max(bounds.left() + 4, x)
        y = max(bounds.top() + 4, y)
        self.move(x, y)
        self.show()
        self.raise_()


class ThemedToolTipFilter(QObject):
    '''拦截 QEvent.ToolTip，统一用自绘提示框显示。'''

    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(90)
        self._hide_timer.timeout.connect(self._hide)

    def _hide(self):
        tip = ThemedToolTip.instance()
        if tip.isVisible():
            tip.hide()

    def eventFilter(self, obj, event):
        event_type = event.type()
        if event_type == QEvent.ToolTip:
            if isinstance(obj, QWidget) and not isinstance(obj, ThemedToolTip):
                text = obj.toolTip()
                if text and obj.isEnabled() and obj.isVisible():
                    self._hide_timer.stop()
                    position = None
                    try:
                        position = event.globalPos()
                    except (AttributeError, TypeError):
                        position = QCursor.pos()
                    ThemedToolTip.instance().show_text(text, position)
                    event.accept()
                    return True
            self._hide()
            return False
        if event_type in (
            QEvent.Leave,
            QEvent.Hide,
            QEvent.MouseButtonPress,
            QEvent.Wheel,
            QEvent.WindowDeactivate,
            QEvent.ApplicationDeactivate,
        ):
            if obj is not ThemedToolTip.instance():
                if event_type in (QEvent.MouseButtonPress, QEvent.Wheel):
                    self._hide()
                else:
                    self._hide_timer.start()
        return False


def install_themed_tooltips(app):
    existing = getattr(app, '_themed_tooltip_filter', None)
    if existing is not None:
        return existing
    tooltip_filter = ThemedToolTipFilter(app)
    app._themed_tooltip_filter = tooltip_filter
    app.installEventFilter(tooltip_filter)
    return tooltip_filter


__all__ = ['ThemedToolTip', 'ThemedToolTipFilter', 'install_themed_tooltips']
