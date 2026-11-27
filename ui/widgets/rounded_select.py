"""版本类型等轻量下拉选择器。

QComboBox 的 popup 由 Qt 内部容器管理，样式、hover 和首次定位很容易受
平台影响。这个组件把按钮和弹层都自己画：按钮负责显示当前项，Qt.Popup
里的 QListWidget 只负责列表，hover/选中状态都由显式索引控制。
"""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QFontMetrics, QPainter, QPainterPath, QPalette, QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QGraphicsOpacityEffect,
    QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QStyle,
    QStyledItemDelegate, QVBoxLayout,
)

ACCENT = QColor('#88DDDD')
ACCENT_TEXT = QColor('#102020')
BUTTON_RADIUS = 8
POPUP_RADIUS = 10
ITEM_RADIUS = 6
ROW_HEIGHT = 34
MAX_VISIBLE_ROWS = 10


def _opaque(color):
    '''下拉必须自身不透明，避免透出下方页面内容。'''

    value = QColor(color)
    value.setAlpha(255)
    return value


class _SelectItemDelegate(QStyledItemDelegate):
    """只给当前选中项和鼠标真正所在项画背景。"""

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(3, 2, -3, -2)
        selected = bool(option.state & QStyle.State_Selected)
        view = option.widget
        hovered = index.row() == getattr(view, '_hover_index', -1)
        if selected or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(
                ACCENT if selected else QColor(136, 221, 221, 42)
            )
            painter.drawRoundedRect(
                QRectF(rect), ITEM_RADIUS, ITEM_RADIUS
            )
        text = str(index.data(Qt.DisplayRole) or '')
        font = option.font
        painter.setFont(font)
        painter.setPen(
            ACCENT_TEXT
            if selected
            else option.palette.color(QPalette.Text)
        )
        text_rect = option.rect.adjusted(12, 0, -12, 0)
        text = QFontMetrics(font).elidedText(
            text, Qt.ElideRight, max(1, text_rect.width())
        )
        painter.drawText(
            text_rect, Qt.AlignLeft | Qt.AlignVCenter, text
        )
        painter.restore()


class _SelectList(QListWidget):
    item_chosen = Signal(int)
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_index = -1
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setItemDelegate(_SelectItemDelegate(self))
        self.setStyleSheet(
            'QListWidget { background: transparent; border: none;'
            ' padding: 0px; outline: 0; }'
            'QListWidget::item { background: transparent; border: none;'
            ' padding: 0px; margin: 0px; }'
            'QScrollBar:vertical { background: transparent; width: 8px;'
            ' margin: 2px; border-radius: 4px; }'
            'QScrollBar::handle:vertical { background: palette(mid);'
            ' min-height: 20px; border-radius: 4px; }'
            'QScrollBar::handle:vertical:hover { background: palette(dark); }'
            'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {'
            ' height: 0px; }'
            'QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {'
            ' background: transparent; }'
        )
        self.viewport().installEventFilter(self)

    def _set_hover_index(self, row):
        row = int(row) if row is not None else -1
        if row < 0 or row >= self.count():
            row = -1
        if row == self._hover_index:
            return
        self._hover_index = row
        self.viewport().update()

    def clear_hover(self):
        self._set_hover_index(-1)

    def eventFilter(self, obj, event):
        if obj is self.viewport():
            if event.type() == QEvent.MouseMove:
                index = self.indexAt(event.position().toPoint())
                self._set_hover_index(index.row() if index.isValid() else -1)
            elif event.type() == QEvent.Leave:
                self.clear_hover()
        return super().eventFilter(obj, event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                self.item_chosen.emit(index.row())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            row = self.currentRow()
            if row >= 0:
                self.item_chosen.emit(row)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self.clear_hover()
        super().hideEvent(event)


class _SelectPopup(QFrame):
    item_chosen = Signal(int)

    def __init__(self, owner):
        # 不用 Qt.Popup：Qt.Popup 会因为窗口激活/鼠标抓取等平台事件
        # 自行 hide，表现为偶发“点开又收起”。这里由 eventFilter 明确
        # 判断点击是否在弹层外，只有真正的操作才关闭。
        super().__init__(
            owner,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint,
        )
        self._owner = owner
        self._open_anim = None
        self._opacity_effect = None
        self._final_pos = QPoint()
        self._slide = 0
        self._app_filter = False
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoMousePropagation, True)
        self.setObjectName('RoundedSelectPopup')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)
        self.list = _SelectList(self)
        layout.addWidget(self.list)
        self.list.item_chosen.connect(self._choose)
        self.list.cancel_requested.connect(self.hide)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # 不要用 self.palette().Base：父级半透明 QSS 会把子控件
        # 的 Base/Window 解析成 rgba(128,128,128,0.13)，看起来是透明的。
        painter.setBrush(
            _opaque(QApplication.palette().color(QPalette.Base))
        )
        painter.setPen(
            QPen(_opaque(self.palette().color(QPalette.Mid)), 1)
        )
        painter.drawRoundedRect(rect, POPUP_RADIUS, POPUP_RADIUS)
        painter.end()

    def prepare_open(self, final_pos, slide):
        '''在 show 之前把弹层放到起点，避免第一帧闪到最终位置。'''

        self.stop_open_animation()
        self._final_pos = QPoint(final_pos)
        self._slide = int(slide)
        self.move(final_pos.x(), final_pos.y() + self._slide)
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)
        self._opacity_effect = effect

    def start_open_animation(self):
        if self._opacity_effect is None:
            return
        anim = QVariantAnimation(self)
        anim.setDuration(150)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._apply_open_progress)
        anim.finished.connect(self._finish_open_animation)
        self._open_anim = anim
        anim.start()

    def _apply_open_progress(self, value):
        try:
            progress = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        if self._opacity_effect is not None:
            self._opacity_effect.setOpacity(progress)
        x = self._final_pos.x()
        y = self._final_pos.y() + int(round(self._slide * (1.0 - progress)))
        self.move(x, y)

    def _finish_open_animation(self):
        if self._opacity_effect is not None:
            self._opacity_effect.setOpacity(1.0)
            self.setGraphicsEffect(None)
            self._opacity_effect = None
        self.move(self._final_pos)
        self._open_anim = None

    def stop_open_animation(self):
        anim, effect = self._open_anim, self._opacity_effect
        self._open_anim = None
        self._opacity_effect = None
        if anim is not None:
            try:
                anim.stop()
                anim.deleteLater()
            except RuntimeError:
                pass
        if effect is not None:
            try:
                self.setGraphicsEffect(None)
            except RuntimeError:
                pass

    def rebuild(self, items, current_index):
        self.list.clear()
        for text, data in items:
            item = QListWidgetItem(str(text))
            item.setData(Qt.UserRole, data)
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.list.addItem(item)
        self.list.clear_hover()
        if 0 <= current_index < self.list.count():
            self.list.setCurrentRow(current_index)
        else:
            self.list.setCurrentRow(-1)
        self.list.scrollToTop()

    def preferred_size(self, min_width):
        rows = max(1, min(self.list.count(), MAX_VISIBLE_ROWS))
        width = max(1, int(min_width))
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item is not None:
                width = max(
                    width,
                    self.list.sizeHintForColumn(0) + 24,
                    QFontMetrics(item.font()).horizontalAdvance(item.text())
                    + 48,
                )
        return QSize(width, rows * ROW_HEIGHT + 12)

    def showEvent(self, event):
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None and not self._app_filter:
            app.installEventFilter(self)
            self._app_filter = True
        self.list.clear_hover()
        self.start_open_animation()
        self.raise_()
        self.activateWindow()
        self.list.setFocus(Qt.PopupFocusReason)

    def hideEvent(self, event):
        if self._app_filter:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._app_filter = False
        self.stop_open_animation()
        self.list.clear_hover()
        super().hideEvent(event)
        self._owner._on_popup_hidden()

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self.update()
        super().changeEvent(event)

    @staticmethod
    def _global_pos(event):
        try:
            return event.globalPosition().toPoint()
        except AttributeError:
            try:
                return event.globalPos()
            except AttributeError:
                return QPoint()

    def _hide_from_outside(self, event):
        pos = self._global_pos(event)
        if self.frameGeometry().contains(pos):
            return False
        try:
            owner_click = self._owner.rect().contains(
                self._owner.mapFromGlobal(pos)
            )
            just_opened = (
                time.monotonic()
                - float(getattr(self._owner, '_opened_at', 0.0))
                < 0.28
            )
        except (RuntimeError, AttributeError):
            owner_click = False
            just_opened = False
        if owner_click and just_opened:
            # 开启动画的 280ms 内，忽略落在按钮上的第二次 press，
            # 防止快速双击/触控板双触发把刚展开的弹层关掉。
            return True
        self.hide()
        # 点击下拉按钮本身时吞掉这次 press，避免按钮收到 release
        # 后再次展开，出现“关掉又打开”。
        return owner_click

    def eventFilter(self, obj, event):
        event_type = event.type()
        if event_type in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            return self._hide_from_outside(event)
        if event_type == QEvent.Wheel:
            return self._hide_from_outside(event)
        if event_type == QEvent.ApplicationDeactivate:
            # 某些平台在弹层刚 show 完的同一轮事件里会补发一次
            # ApplicationDeactivate；不能把它当成用户切走。
            try:
                opened_at = float(
                    getattr(self._owner, '_opened_at', 0.0)
                )
            except (RuntimeError, AttributeError, TypeError):
                opened_at = 0.0
            if time.monotonic() - opened_at > 1.0:
                self.hide()
        return super().eventFilter(obj, event)

    def _choose(self, row):
        self.item_chosen.emit(row)
        self.hide()


class RoundedSelect(QPushButton):
    """自绘的圆角单选下拉，API 覆盖版本分类下拉需要的 QComboBox 子集。"""

    currentIndexChanged = Signal(int)
    activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._current_index = -1
        self._hover = False
        self._open_on_release = False
        self._suppress_release = False
        self._opened_at = 0.0
        self._arrow_angle = 0.0
        self._arrow_anim = None
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.setInterval(0)
        self._open_timer.timeout.connect(self._open_from_release)
        self._popup = _SelectPopup(self)
        self._popup.item_chosen.connect(self._choose)
        self.setText('')
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(168)
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    # ---------- 数据 API ----------
    def count(self):
        return len(self._items)

    def addItem(self, text, data=None):
        self._items.append((str(text), data))
        first = self._current_index < 0
        if first:
            self._current_index = 0
        self.updateGeometry()
        self.update()
        if first and not self.signalsBlocked():
            self.currentIndexChanged.emit(0)

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def clear(self):
        self.hidePopup()
        had_current = self._current_index >= 0
        self._items.clear()
        self._current_index = -1
        self.updateGeometry()
        self.update()
        if had_current and not self.signalsBlocked():
            self.currentIndexChanged.emit(-1)

    def itemData(self, index):
        if 0 <= index < len(self._items):
            return self._items[index][1]
        return None

    def itemText(self, index):
        if 0 <= index < len(self._items):
            return self._items[index][0]
        return ''

    def currentIndex(self):
        return self._current_index

    def currentData(self):
        return self.itemData(self._current_index)

    def currentText(self):
        return self.itemText(self._current_index)

    def findData(self, data):
        for index, (_text, item_data) in enumerate(self._items):
            if item_data == data:
                return index
        return -1

    def findText(self, text):
        for index, (item_text, _data) in enumerate(self._items):
            if item_text == str(text):
                return index
        return -1

    def setCurrentIndex(self, index):
        index = int(index)
        if index < -1:
            index = -1
        if index >= len(self._items):
            index = len(self._items) - 1
        if index == self._current_index:
            return
        self._current_index = index
        if self._popup.isVisible():
            self._popup.list.setCurrentRow(index)
        self.updateGeometry()
        self.update()
        self.currentIndexChanged.emit(index)

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        if not enabled and hasattr(self, '_popup'):
            self.hidePopup()

    def setCurrentData(self, data):
        index = self.findData(data)
        if index >= 0:
            self.setCurrentIndex(index)

    def sizeHint(self):
        width = max(96, self.minimumWidth())
        metrics = QFontMetrics(self.font())
        width = max(
            width, metrics.horizontalAdvance(self.currentText()) + 48
        )
        return QSize(width, 34)

    # ---------- 展开/收起 ----------
    def showPopup(self):
        self._open_timer.stop()
        if not self._items or self._popup.isVisible():
            return
        self._popup.list.setFont(self.font())
        self._popup.rebuild(self._items, self._current_index)
        size = self._popup.preferred_size(self.width())
        self._popup.setFixedSize(size)
        self._popup.layout().activate()
        if self._current_index >= MAX_VISIBLE_ROWS:
            item = self._popup.list.item(self._current_index)
            if item is not None:
                self._popup.list.scrollToItem(
                    item, QAbstractItemView.EnsureVisible
                )
        anchor = self.mapToGlobal(QPoint(0, self.height() + 2))
        top = self.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(anchor) or self.screen()
        area = screen.availableGeometry() if screen is not None else None
        x, y = anchor.x(), anchor.y()
        if area is not None:
            x = min(
                max(x, area.left()),
                max(area.left(), area.right() - size.width() + 1),
            )
            above = top.y() - size.height() - 2
            if y + size.height() > area.bottom() and above >= area.top():
                y = above
            else:
                y = min(
                    y, max(area.top(), area.bottom() - size.height() + 1)
                )
        # 弹层从按钮方向轻微滑入并淡入，形成展开动画。
        slide = -5 if y >= top.y() else 5
        self._popup.prepare_open(QPoint(x, y), slide)
        self._popup.show()
        self._opened_at = time.monotonic()
        self._animate_arrow(180.0)
        self.update()

    def hidePopup(self):
        self._open_timer.stop()
        self._popup.hide()

    def isPopupVisible(self):
        return self._popup.isVisible()

    def _choose(self, row):
        if 0 <= row < len(self._items):
            self.setCurrentIndex(row)
            self.activated.emit(row)
        self.hidePopup()
        self.setFocus(Qt.OtherFocusReason)

    def _animate_arrow(self, target):
        start = float(self._arrow_angle)
        target = float(target)
        old = self._arrow_anim
        self._arrow_anim = None
        if old is not None:
            try:
                old.stop()
                old.deleteLater()
            except RuntimeError:
                pass
        if abs(start - target) < 0.5:
            self._arrow_angle = target
            self.update()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(160)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._set_arrow_angle)
        anim.finished.connect(
            lambda a=anim: self._finish_arrow_animation(a)
        )
        self._arrow_anim = anim
        anim.start()

    def _set_arrow_angle(self, value):
        try:
            self._arrow_angle = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _finish_arrow_animation(self, anim):
        if self._arrow_anim is anim:
            self._arrow_anim = None

    def _on_popup_hidden(self):
        self._animate_arrow(0.0)
        self.update()

    # ---------- 交互 ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._popup.isVisible():
                # 触控板/快速双击会在展开后的很短时间内再送来一次
                # press。这个 press 不能当成“再次点击关闭”，否则会
                # 表现为点开后又自动收起。
                if time.monotonic() - self._opened_at < 0.28:
                    self._suppress_release = True
                    event.accept()
                    return
                self.hidePopup()
                self._suppress_release = True
            else:
                self._open_on_release = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._suppress_release:
            self._suppress_release = False
            event.accept()
            return
        if self._open_on_release:
            self._open_on_release = False
            if self.rect().contains(event.position().toPoint()):
                # 延迟到释放事件完全结束后再 show。否则 Windows 上
                # 弹层抢到鼠标时可能把当前释放判成“点击外部”而立即收起。
                self._open_timer.start()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _open_from_release(self):
        if not self.isVisible() or not self.isEnabled():
            return
        if not self._popup.isVisible():
            self.showPopup()

    def keyPressEvent(self, event):
        if event.key() in (
            Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space, Qt.Key_Down,
        ):
            if not self._popup.isVisible():
                self.showPopup()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.hidePopup()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.hidePopup()
        super().hideEvent(event)

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self.update()
        super().changeEvent(event)

    # ---------- 绘制 ----------
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        palette = self.palette()
        active = (
            self._hover
            or self.hasFocus()
            or self._popup.isVisible()
        )
        border = ACCENT if active else _opaque(
            palette.color(QPalette.Mid)
        )
        painter.setBrush(
            _opaque(QApplication.palette().color(QPalette.Base))
        )
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(
            rect, BUTTON_RADIUS, BUTTON_RADIUS
        )
        text_rect = self.rect().adjusted(12, 0, -30, 0)
        painter.setFont(self.font())
        if self.isEnabled():
            text_color = palette.color(QPalette.Text)
        else:
            text_color = palette.color(QPalette.Disabled, QPalette.Text)
        painter.setPen(text_color)
        text = QFontMetrics(self.font()).elidedText(
            self.currentText(), Qt.ElideRight, max(1, text_rect.width())
        )
        painter.drawText(
            text_rect, Qt.AlignLeft | Qt.AlignVCenter, text
        )
        center_x = self.width() - 16
        center_y = self.height() / 2
        painter.save()
        painter.translate(center_x, center_y)
        painter.rotate(self._arrow_angle)
        chevron = QPainterPath()
        chevron.moveTo(-4, -2)
        chevron.lineTo(0, 2.5)
        chevron.lineTo(4, -2)
        pen = QPen(
            ACCENT if active else palette.color(QPalette.Text), 1.8
        )
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(chevron)
        painter.restore()
        painter.end()


__all__ = ['RoundedSelect']
