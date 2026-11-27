"""圆角下拉框与弹出菜单。

Qt 的 QComboBox 弹出列表是顶层 popup，QSS 只能给内部 view 画圆角；
这里在 popup 显示后给它设置真正的圆角 mask，并用委托绘制圆角选中项。
"""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRectF, Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPalette, QRegion,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QGraphicsOpacityEffect,
    QMenu, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
)

RADIUS = 10
ITEM_RADIUS = 6
ACCENT = QColor('#88DDDD')
ACCENT_TEXT = QColor('#102020')


def _rounded_region(rect, radius=RADIUS):
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(0, 0, max(1, rect.width()), max(1, rect.height())),
        radius,
        radius,
    )
    return QRegion(path.toFillPolygon().toPolygon())


def apply_popup_rounding(widget):
    if widget is None:
        return
    try:
        widget.setAttribute(Qt.WA_TranslucentBackground, True)
        widget.setMask(_rounded_region(widget.rect()))
        widget.installEventFilter(_POPUP_FILTER)
    except (RuntimeError, AttributeError):
        pass


class RoundedItemDelegate(QStyledItemDelegate):
    """在 combo popup 内绘制圆角 hover/选中背景。"""

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.State_Selected)
        view = option.widget
        hovered = (
            view is not None
            and index.row() == getattr(view, '_hover_index', -1)
        )
        if selected or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(
                ACCENT if selected else QColor(136, 221, 221, 42)
            )
            painter.drawRoundedRect(
                QRectF(rect), ITEM_RADIUS, ITEM_RADIUS
            )
        view_option = QStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)
        view_option.state &= ~(
            QStyle.State_Selected
            | QStyle.State_HasFocus
            | QStyle.State_MouseOver
        )
        from PySide6.QtGui import QBrush
        view_option.backgroundBrush = QBrush(Qt.NoBrush)
        if selected:
            view_option.palette.setColor(QPalette.Text, ACCENT_TEXT)
            view_option.palette.setColor(QPalette.HighlightedText, ACCENT_TEXT)
        QApplication.style().drawControl(
            QStyle.CE_ItemViewItem, view_option, painter, option.widget
        )
        painter.restore()


class RoundedComboBox(QComboBox):
    """所有 QComboBox 的统一入口，popup 外框和选中项都圆角。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaxVisibleItems(10)
        self.view().setAlternatingRowColors(False)
        self.view().setMouseTracking(True)
        self.view().setItemDelegate(RoundedItemDelegate(self.view()))
        self.view().setProperty('_theme_ignore', True)
        self._sync_view_palette()
        self.view().viewport().installEventFilter(self)
        self.view().entered.connect(self._set_hover_index)
        self._hover_index = -1
        # 委托拿到的 option.widget 是内部 view，不是 combo 本身；
        # 同步一份到 view，保证 delegate 能读到显式 hover。
        self.view()._hover_index = -1
        self._open_on_release = False
        self._suppress_release = False
        self._opened_at = 0.0
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.setInterval(0)
        self._open_timer.timeout.connect(self._open_from_release)
        self._popup_anim = None
        self._popup_effect = None
        self._popup_hiding = False

    def _sync_view_palette(self):
        # 半透明父级 QSS 会把子控件 Base 解析成透明灰色；combo popup
        # 的全局 QSS 用 palette(base) 画背景，必须换成应用级不透明色。
        try:
            palette = self.view().palette()
            base = QColor(QApplication.palette().color(QPalette.Base))
            base.setAlpha(255)
            palette.setColor(QPalette.Base, base)
            palette.setColor(QPalette.Window, base)
            self.view().setPalette(palette)
        except (RuntimeError, AttributeError):
            pass

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self._sync_view_palette()
        super().changeEvent(event)

    def _set_hover_index(self, index):
        row = index.row() if index is not None and index.isValid() else -1
        if row == self._hover_index:
            return
        self._hover_index = row
        self.view()._hover_index = row
        self.view().viewport().update()

    def _clear_hover(self):
        if self._hover_index < 0:
            return
        self._hover_index = -1
        self.view()._hover_index = -1
        self.view().viewport().update()

    def _should_open_from_click(self, event):
        if not self.isEditable():
            return True
        return event.position().x() >= self.width() - 30

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._should_open_from_click(event):
            if self.view().isVisible() and time.monotonic() - self._opened_at < 0.28:
                self._suppress_release = True
                event.accept()
                return
            self._open_on_release = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, '_suppress_release', False):
            self._suppress_release = False
            event.accept()
            return
        if getattr(self, '_open_on_release', False):
            self._open_on_release = False
            if self.rect().contains(event.position().toPoint()):
                # 等释放事件处理完再展开，避免 popup 抢鼠标后把这次
                # 释放误判为外部点击，表现为“点一下马上收起”。
                self._open_timer.start()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _open_from_release(self):
        if self.isVisible() and self.isEnabled():
            self.showPopup()

    def _position_popup(self):
        try:
            popup = self.view().window()
            if not popup.isVisible():
                return
            view = self.view()
            current = view.currentIndex()
            if current.isValid():
                rect = view.visualRect(current)
                if not view.viewport().rect().contains(rect):
                    view.scrollTo(
                        current, QAbstractItemView.EnsureVisible
                    )

            anchor = self.mapToGlobal(QPoint(0, self.height()))
            top = self.mapToGlobal(QPoint(0, 0))
            screen = QApplication.screenAt(anchor) or self.screen()
            area = screen.availableGeometry() if screen is not None else None
            width = max(1, popup.width(), popup.sizeHint().width())
            height = max(1, popup.height(), popup.sizeHint().height())
            x = anchor.x()
            y = anchor.y()
            if area is not None:
                x = min(
                    max(x, area.left()),
                    max(area.left(), area.right() - width + 1),
                )
                below = y
                above = top.y() - height
                if below + height > area.bottom() and above >= area.top():
                    y = above
                else:
                    y = min(
                        below,
                        max(area.top(), area.bottom() - height + 1),
                    )
            popup.move(x, y)
        except RuntimeError:
            pass

    def showPopup(self):
        self._open_timer.stop()
        self._popup_hiding = False
        self._clear_hover()
        super().showPopup()
        self._opened_at = time.monotonic()
        self._position_popup()
        QTimer.singleShot(0, lambda: self._round_popup())
        QTimer.singleShot(0, self._animate_popup_open)
        QTimer.singleShot(0, self._position_popup)
        QTimer.singleShot(30, self._position_popup)

    def _stop_popup_animation(self):
        animation, effect = self._popup_anim, self._popup_effect
        self._popup_anim = None
        self._popup_effect = None
        if animation is not None:
            animations = (
                animation if isinstance(animation, (tuple, list))
                else (animation,)
            )
            for item in animations:
                try:
                    item.stop()
                except RuntimeError:
                    pass
        if effect is not None:
            try:
                popup = self.view().window()
                popup.setGraphicsEffect(None)
            except RuntimeError:
                pass

    def _animate_popup_open(self):
        try:
            popup = self.view().window()
            if not popup.isVisible():
                return
            self._stop_popup_animation()
            effect = QGraphicsOpacityEffect(popup)
            effect.setOpacity(0.0)
            popup.setGraphicsEffect(effect)
            fade = QPropertyAnimation(effect, b'opacity', self)
            fade.setDuration(120)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.OutCubic)
            fade.finished.connect(
                lambda: self._finish_popup_open(popup)
            )
            self._popup_anim = (fade,)
            self._popup_effect = effect
            fade.start()
        except RuntimeError:
            pass

    def _finish_popup_open(self, popup):
        try:
            popup.setGraphicsEffect(None)
        except RuntimeError:
            pass
        self._popup_anim = None
        self._popup_effect = None

    def hidePopup(self):
        if self._popup_hiding:
            return
        self._open_timer.stop()
        self._clear_hover()
        try:
            popup = self.view().window()
            animation = self._popup_anim
        except RuntimeError:
            super().hidePopup()
            return
        if animation is None or not popup.isVisible():
            super().hidePopup()
            return
        self._stop_popup_animation()
        effect = QGraphicsOpacityEffect(popup)
        effect.setOpacity(1.0)
        popup.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b'opacity', self)
        fade.setDuration(100)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)

        def finish():
            self._popup_hiding = True
            try:
                popup.setGraphicsEffect(None)
            except RuntimeError:
                pass
            self._popup_anim = None
            self._popup_effect = None
            QComboBox.hidePopup(self)
            self._popup_hiding = False

        fade.finished.connect(finish)
        self._popup_anim = (fade,)
        self._popup_effect = effect
        fade.start()

    def _round_popup(self):
        try:
            popup = self.view().window()
        except RuntimeError:
            return
        apply_popup_rounding(popup)
        try:
            popup.installEventFilter(self)
        except RuntimeError:
            pass

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize:
            try:
                if self.view().window() is obj:
                    apply_popup_rounding(obj)
            except RuntimeError:
                pass
        elif event.type() in (QEvent.MouseMove, QEvent.Leave):
            try:
                view = self.view()
                if obj is view.viewport():
                    if event.type() == QEvent.Leave:
                        self._clear_hover()
                    else:
                        self._set_hover_index(
                            view.indexAt(event.position().toPoint())
                        )
            except RuntimeError:
                pass
        return super().eventFilter(obj, event)


class PopupRoundFilter(QObject):
    """给 QMenu 等 Qt 内部 popup 补圆角 mask。"""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QMenu):
            QTimer.singleShot(0, lambda w=obj: apply_popup_rounding(w))
        elif event.type() == QEvent.Resize and isinstance(obj, QMenu):
            apply_popup_rounding(obj)
        return False


_POPUP_FILTER = PopupRoundFilter(QApplication.instance())


def install_popup_rounding(app=None):
    app = app or QApplication.instance()
    if app is None:
        return None
    global _POPUP_FILTER
    _POPUP_FILTER = PopupRoundFilter(app)
    app.installEventFilter(_POPUP_FILTER)
    return _POPUP_FILTER


__all__ = [
    'PopupRoundFilter', 'RoundedComboBox', 'RoundedItemDelegate',
    'apply_popup_rounding', 'install_popup_rounding',
]
