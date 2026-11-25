"""圆角下拉框与弹出菜单。

Qt 的 QComboBox 弹出列表是顶层 popup，QSS 只能给内部 view 画圆角；
这里在 popup 显示后给它设置真正的圆角 mask，并用委托绘制圆角选中项。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QRegion
from PySide6.QtWidgets import (
    QApplication, QComboBox, QMenu, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem,
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
        hovered = bool(option.state & QStyle.State_MouseOver)
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
        self.view().setItemDelegate(RoundedItemDelegate(self.view()))
        self.view().setProperty('_theme_ignore', True)

    def showPopup(self):
        super().showPopup()
        QTimer.singleShot(0, lambda: self._round_popup())

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
