'''折叠式侧边导航：图标栏 + 悬停展开后的图标和文字。'''

from __future__ import annotations

from PySide6.QtCore import Property, QRectF, QSize, Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QListWidget, QStyle, QStyledItemDelegate

from ui.widgets.painted_icons import painted_icon

NAV_ICON_ROLE = int(Qt.UserRole) + 10
NAV_HEADER_ROLE = int(Qt.UserRole) + 11


class SideNavDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.collapsed = True
        self.light = False
        self._text_opacity = 0.0

    def get_text_opacity(self):
        return self._text_opacity

    def set_text_opacity(self, value):
        self._text_opacity = max(0.0, min(1.0, float(value)))
        parent = self.parent()
        if parent is not None:
            parent.viewport().update()

    textOpacity = Property(float, get_text_opacity, set_text_opacity)

    def set_collapsed(self, collapsed: bool):
        self.collapsed = bool(collapsed)

    def set_light(self, light: bool):
        self.light = bool(light)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(option.rect)
        if index.data(NAV_HEADER_ROLE):
            if self.collapsed:
                line_color = QColor(255, 255, 255, 24) if not self.light else QColor(0, 0, 0, 22)
                painter.setPen(QPen(line_color, 1))
                y = int(rect.center().y())
                painter.drawLine(int(rect.left()) + 22, y, int(rect.right()) - 22, y)
            else:
                painter.setOpacity(self._text_opacity)
                painter.setPen(QColor('#8b929d' if not self.light else '#707783'))
                font = QFont(option.font)
                font.setPointSizeF(8.2)
                painter.setFont(font)
                painter.drawText(
                    rect.adjusted(33, 0, -8, 0),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    index.data(Qt.DisplayRole) or '',
                )
            painter.restore()
            return

        selected = bool(option.state & QStyle.State_Selected)
        hover_row = getattr(self.parent(), '_hover_row', -1)
        hover = bool(option.state & QStyle.State_MouseOver) or hover_row == index.row()
        inset = 8.0 if self.collapsed else 6.0
        body = rect.adjusted(inset, 2.0, -inset, -2.0)
        if selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#0078d4'))
            painter.drawRoundedRect(body, 9.0, 9.0)
        elif hover:
            color = (
                QColor(0, 0, 0, 22)
                if self.light else QColor(255, 255, 255, 28)
            )
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(body, 9.0, 9.0)

        icon_name = index.data(NAV_ICON_ROLE) or ''
        size = 24
        if selected:
            icon_color = '#ffffff'
        else:
            icon_color = '#4f5866' if self.light else '#c5cbd6'
        if icon_name:
            icon = painted_icon(str(icon_name), size, icon_color, light_safe=True)
            pixmap = icon.pixmap(QSize(size, size))
            # 图标中心固定在简化栏的中线上，展开时只向右延展文字。
            x = 45.0 - size / 2.0
            y = body.center().y() - size / 2.0
            painter.drawPixmap(int(round(x)), int(round(y)), pixmap)

        if not self.collapsed:
            text = str(index.data(Qt.DisplayRole) or '')
            font = QFont(option.font)
            font.setPointSizeF(10.3)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            text_rect = rect.adjusted(62.0, 0, -8.0, 0)
            text = metrics.elidedText(
                text, Qt.ElideRight, max(0, int(text_rect.width()))
            )
            painter.setOpacity(self._text_opacity)
            painter.setPen(
                QColor('#ffffff') if selected
                else QColor('#202124' if self.light else '#e8ebf1')
            )
            painter.drawText(
                text_rect, Qt.AlignLeft | Qt.AlignVCenter, text
            )
        elif selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#0078d4'))
            painter.drawRoundedRect(
                QRectF(rect.left() + 1.0, rect.top() + 9.0, 3.0, rect.height() - 18.0),
                1.5,
                1.5,
            )
        painter.restore()

    def sizeHint(self, option, index):
        if index.data(NAV_HEADER_ROLE):
            return QSize(0, 18)
        return QSize(0, 38)


class SideNavList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._delegate = SideNavDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSpacing(0)
        self._hover_row = -1
        self.itemEntered.connect(self._on_item_entered)
        self.setStyleSheet(
            'QListWidget { background:transparent; border:none; outline:none; }'
            'QListWidget::item { background:transparent; border:none; }'
        )

    def _on_item_entered(self, item):
        try:
            row = self.row(item)
        except RuntimeError:
            return
        if row != self._hover_row:
            self._hover_row = row
            self.viewport().update()

    def leaveEvent(self, event):
        self._hover_row = -1
        self.viewport().update()
        super().leaveEvent(event)

    def set_collapsed(self, collapsed: bool):
        self._delegate.set_collapsed(collapsed)
        self._delegate.set_text_opacity(0.0 if collapsed else 1.0)
        self.viewport().update()

    def animate_expanded(self, expanded: bool, duration=190, animate=True):
        animation = getattr(self, '_text_anim', None)
        if animation is not None:
            try:
                animation.stop()
            except RuntimeError:
                pass
            self._text_anim = None
        if expanded:
            self._delegate.set_collapsed(False)
            if not animate:
                self._delegate.set_text_opacity(1.0)
                return
            animation = QPropertyAnimation(
                self._delegate, b'textOpacity', self
            )
            animation.setDuration(int(duration))
            animation.setStartValue(self._delegate.get_text_opacity())
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            self._text_anim = animation
            animation.start()
            return
        if not animate:
            self._delegate.set_text_opacity(0.0)
            self._delegate.set_collapsed(True)
            return
        animation = QPropertyAnimation(
            self._delegate, b'textOpacity', self
        )
        animation.setDuration(int(duration))
        animation.setStartValue(self._delegate.get_text_opacity())
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(
            lambda: self._delegate.set_collapsed(True)
        )
        self._text_anim = animation
        animation.start()

    def set_light(self, light: bool):
        self._delegate.set_light(light)
        self.viewport().update()


__all__ = [
    'NAV_HEADER_ROLE', 'NAV_ICON_ROLE', 'SideNavDelegate', 'SideNavList',
]
