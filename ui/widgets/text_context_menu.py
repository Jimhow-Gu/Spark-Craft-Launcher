"""统一的文本输入框右键菜单（剪切/复制/粘贴/全选）。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication, QLineEdit, QMenu, QPlainTextEdit, QTextEdit,
)


class TextContextMenuFilter(QObject):
    """用启动器自己的深色菜单替换系统右键菜单。"""

    MENU_STYLE = """
        QMenu {
            background: #20232b;
            color: #f0f0f0;
            border: 1px solid rgba(255,255,255,0.18);
            border-radius: 8px;
            padding: 5px;
        }
        QMenu::item {
            padding: 6px 26px 6px 18px;
            border-radius: 5px;
        }
        QMenu::item:selected {
            background: #0078d4;
            color: white;
        }
        QMenu::item:disabled {
            color: rgba(255,255,255,0.32);
        }
        QMenu::separator {
            height: 1px;
            background: rgba(255,255,255,0.12);
            margin: 4px 8px;
        }
    """

    def _selection_state(self, widget):
        if isinstance(widget, QLineEdit):
            return bool(widget.hasSelectedText()), widget.isReadOnly()
        cursor = widget.textCursor()
        return bool(cursor.hasSelection()), widget.isReadOnly()

    def eventFilter(self, obj, event):
        if not isinstance(obj, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return super().eventFilter(obj, event)
        if event.type() != QEvent.ContextMenu:
            return super().eventFilter(obj, event)

        has_selection, read_only = self._selection_state(obj)
        clipboard_has_text = bool(QApplication.clipboard().text())
        menu = QMenu(obj)
        menu.setStyleSheet(self.MENU_STYLE)

        cut_action = menu.addAction('剪切')
        cut_action.setEnabled(has_selection and not read_only)
        cut_action.triggered.connect(obj.cut)

        copy_action = menu.addAction('复制')
        copy_action.setEnabled(has_selection)
        copy_action.triggered.connect(obj.copy)

        paste_action = menu.addAction('粘贴')
        paste_action.setEnabled(clipboard_has_text and not read_only)
        paste_action.triggered.connect(obj.paste)

        menu.addSeparator()
        select_action = menu.addAction('全选')
        select_action.triggered.connect(obj.selectAll)

        menu.exec(event.globalPos())
        menu.deleteLater()
        return True
