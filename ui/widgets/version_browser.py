"""下载页版本浏览：虚拟化两行版本列表。"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QListWidget, QListWidgetItem, QStyle,
    QStyledItemDelegate,
)

from core.app_font import ui_font

ROW_HEIGHT = 46
ICON_SIZE = 34


class _VersionDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(2, 1, -2, -1)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(136, 221, 221, 20))
            painter.drawRoundedRect(rect, 8, 8)
        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(
                painter,
                QRect(
                    rect.left() + 10,
                    rect.center().y() - ICON_SIZE // 2,
                    ICON_SIZE,
                    ICON_SIZE,
                ),
                Qt.AlignCenter,
            )
        title = str(index.data(Qt.DisplayRole) or '')
        subtitle = str(index.data(Qt.UserRole + 1) or '')
        text_rect = rect.adjusted(54, 0, -10, 0)
        title_font = ui_font(13, QFont.DemiBold)
        subtitle_font = ui_font(11, QFont.Medium)
        painter.setFont(title_font)
        painter.setPen(option.palette.color(QPalette.Text))
        title_rect = QRect(
            text_rect.left(), text_rect.top() + 4,
            text_rect.width(), 20,
        )
        painter.drawText(
            title_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(title_font).elidedText(
                title, Qt.ElideRight, title_rect.width()
            ),
        )
        painter.setFont(subtitle_font)
        painter.setPen(option.palette.color(QPalette.PlaceholderText))
        subtitle_rect = QRect(
            text_rect.left(), text_rect.top() + 24,
            text_rect.width(), 18,
        )
        painter.drawText(
            subtitle_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(subtitle_font).elidedText(
                subtitle, Qt.ElideRight, subtitle_rect.width()
            ),
        )
        painter.restore()


class VersionBrowserView(QListWidget):
    """虚拟化版本列表：只绘制可见行，不再创建每行 QWidget。"""

    version_activated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('VersionBrowser')
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setItemDelegate(_VersionDelegate(self))
        self.setStyleSheet(
            'QListWidget#VersionBrowser { background: palette(base);'
            ' border: 1px solid palette(mid); border-radius: 10px;'
            ' padding: 6px; outline: 0; }'
            'QListWidget#VersionBrowser::item { background: transparent;'
            ' border: none; padding: 0px; margin: 0px; }'
        )
    def _activate_item(self, item):
        version_id = str(item.data(Qt.UserRole) or '')
        if version_id:
            self.version_activated.emit(version_id)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None:
                self._activate_item(item)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def is_empty(self):
        return self.count() == 0

    def version_ids(self):
        return [
            str(self.item(index).data(Qt.UserRole) or '')
            for index in range(self.count())
        ]

    def subtitle_at(self, index):
        item = self.item(index)
        return str(item.data(Qt.UserRole + 1) or '') if item else ''

    def set_data(
        self, *, latest_items=(), sections=(), flat_items=(),
        row_info, icon_provider, all_mode=True, preserve_scroll=False,
    ):
        scroll = self.verticalScrollBar().value()
        self.clear()
        if all_mode:
            items = list(latest_items)
            for _key, _title, section_items in sections:
                items.extend(section_items)
        else:
            items = list(flat_items)
        for version, category in items:
            item = QListWidgetItem()
            item.setText(str(version.get('id') or ''))
            item.setData(Qt.UserRole, str(version.get('id') or ''))
            item.setData(Qt.UserRole + 1, row_info(version, category))
            item.setData(Qt.DecorationRole, icon_provider(version, category))
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.addItem(item)
        if preserve_scroll:
            QTimer.singleShot(
                0, lambda: self.verticalScrollBar().setValue(scroll)
            )
        else:
            self.verticalScrollBar().setValue(0)

    def clear(self):
        super().clear()
        self.verticalScrollBar().setValue(0)

    def fade_in(self):
        self.viewport().update()


__all__ = ['VersionBrowserView']
