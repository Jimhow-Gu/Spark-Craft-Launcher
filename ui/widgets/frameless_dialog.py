'''自绘无边框子窗口基类：统一标题栏、圆角边框、拖动和居中。'''

from __future__ import annotations
from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRectF, QTimer, Qt)
from PySide6.QtGui import (QColor, QIcon, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)
from core.app_font import ui_font

class _RoundedCard(QFrame):
    '''自绘抗锯齿圆角卡片，避免 QSS 圆角在子窗口边缘产生锯齿。'''

    def __init__(self, parent=None, radius=12):
        super().__init__(parent)
        self._radius = int(radius)
        self._light = False
        self.setObjectName('FramelessDialogCard')
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def set_light(self, light: bool):
        self._light = bool(light)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        if self._light:
            painter.fillPath(path, QColor(249, 249, 249, 255))
            painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
        else:
            painter.fillPath(path, QColor(32, 32, 32, 255))
            painter.setPen(QPen(QColor(255, 255, 255, 36), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()


class _DialogTitleBar(QWidget):
    def __init__(self, dialog, title='', parent=None):
        super().__init__(parent)
        self._dialog = dialog
        self._drag = None
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 8, 4)
        layout.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setFont(ui_font(12))
        self.title_label.setStyleSheet('color:#f0f0f0; font-weight:600; background:transparent;')
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        close = QPushButton('✕')
        close.setFixedSize(30, 26)
        close.setToolTip('关闭')
        close.setFocusPolicy(Qt.NoFocus)
        close.setStyleSheet('QPushButton { background:transparent; color:#d7d7d7; border:none; border-radius:6px; } QPushButton:hover { background:#c42b1c; color:white; }')
        close.clicked.connect(dialog.reject)
        layout.addWidget(close)

    def set_title(self, text):
        self.title_label.setText(str(text or ''))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self._dialog.move(event.globalPosition().toPoint() - self._drag)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        event.accept()


class FramelessDialog(QDialog):
    '''所有复杂子窗口的统一外观基类。'''

    def __init__(self, parent=None, title=''):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowIcon(QIcon())
        self._title_text = str(title or '')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        self.card = _RoundedCard()
        self.card.setStyleSheet('QFrame#FramelessDialogCard { background:transparent; color:#f0f0f0; border:none; } QLabel { background:transparent; }')
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        self.title_bar = _DialogTitleBar(self, self._title_text, self.card)
        card_layout.addWidget(self.title_bar)
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(18, 4, 18, 16)
        self.content_layout.setSpacing(10)
        card_layout.addLayout(self.content_layout, 1)

    def setWindowTitle(self, title):
        self._title_text = str(title or '')
        super().setWindowTitle(self._title_text)
        if hasattr(self, 'title_bar'):
            self.title_bar.set_title(self._title_text)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.window().frameGeometry().center()
        else:
            screen = self.screen()
            center = screen.availableGeometry().center() if screen else self.rect().center()
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        QTimer.singleShot(0, self._shake_on_show)

    def _shake_on_show(self):
        '''子窗口出现时轻轻晃动一下。'''

        if not self.isVisible():
            return
        start = self.pos()
        anim = QPropertyAnimation(self, b'pos', self)
        anim.setDuration(420)
        anim.setKeyValueAt(0.0, start)
        anim.setKeyValueAt(0.28, start + QPoint(5, 0))
        anim.setKeyValueAt(0.58, start + QPoint(-4, 0))
        anim.setKeyValueAt(0.82, start + QPoint(2, 0))
        anim.setKeyValueAt(1.0, start)
        anim.setEasingCurve(QEasingCurve.InOutSine)
        self._shake_anim = anim
        anim.start()


class FramelessInputDialog(FramelessDialog):
    '''自绘文本输入窗口，替代 QInputDialog。'''

    ''
    ''
    def __init__(self, parent, title, label, text='', validator=None):
        super().__init__(parent, title)
        self.setWindowModality(Qt.WindowModal if parent is not None else Qt.ApplicationModal)
        self.value = ''
        self._validator = validator
        label_widget = QLabel(label)
        label_widget.setWordWrap(True)
        self.content_layout.addWidget(label_widget)
        self.edit = QLineEdit()
        self.edit.setText(text)
        self.edit.setMinimumHeight(34)
        self._apply_edit_style()
        self.edit.returnPressed.connect(self._accept)
        self.edit.textChanged.connect(self._clear_error)
        self.content_layout.addWidget(self.edit)
        self.error_label = QLabel('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('color:#ff6b6b; font-size:12px; background:transparent;')
        self.error_label.hide()
        self.content_layout.addWidget(self.error_label)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton('取消')
        cancel.setStyleSheet('QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:6px 16px; } QPushButton:hover { background:#4a4a4a; }')
        cancel.clicked.connect(self.reject)
        ok = QPushButton('确定')
        ok.setStyleSheet('QPushButton { background:#0078d4; color:white; border:none; border-radius:6px; padding:6px 18px; } QPushButton:hover { background:#106ebe; }')
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.content_layout.addLayout(row)
        self.setMinimumWidth(380)

    def _apply_edit_style(self, error=False):
        border = '#e05555' if error else 'rgba(255,255,255,0.14)'
        self.edit.setStyleSheet(f'QLineEdit {{ background:rgba(255,255,255,0.05); border:1px solid {border}; border-radius:6px; color:#f0f0f0; padding:5px 8px; }}')

    def _clear_error(self, *_args):
        if hasattr(self, 'error_label') and self.error_label.isVisible():
            self.error_label.hide()
            self._apply_edit_style(False)

    def _accept(self):
        value = self.edit.text().strip()
        self.value = value
        if self._validator is not None:
            error = self._validator(value)
            if error:
                self.error_label.setText(str(error))
                self.error_label.show()
                self._apply_edit_style(True)
                self.edit.setFocus()
                return
        self.accept()


class FramelessListDialog(FramelessDialog):
    '''自绘单选列表窗口，返回选中项的数据。'''

    ''
    def __init__(self, parent, title, items, current=0):
        super().__init__(parent, title)
        self.setWindowModality(Qt.WindowModal if parent is not None else Qt.ApplicationModal)
        self.value = None
        self._armed_item = None
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setStyleSheet('\n            QListWidget { background:rgba(10,12,18,0.45);\n                          border:1px solid rgba(255,255,255,0.16); border-radius:8px;\n                          outline:none; }\n            QListWidget::item { padding:8px 12px; color:rgba(255,255,255,0.85);\n                                border-bottom:1px solid rgba(255,255,255,0.05); }\n            QListWidget::item:selected { background:rgba(0,120,212,0.35); }\n        ')
        for label, data in items:
            item = QListWidgetItem(str(label))
            item.setData(Qt.UserRole, data)
            self.list.addItem(item)
        if items:
            self.list.setCurrentRow(max(0, min(current, len(items) - 1)))
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.itemDoubleClicked.connect((lambda _item: self._accept()))
        self.content_layout.addWidget(self.list, 1)
        hint = QLabel('单击高亮，再次点击同一版本或双击即可选择。')
        hint.setStyleSheet('color:rgba(255,255,255,0.45); font-size:11px;')
        self.content_layout.addWidget(hint)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton('取消')
        cancel.setStyleSheet('QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:6px 16px; } QPushButton:hover { background:#4a4a4a; }')
        cancel.clicked.connect(self.reject)
        ok = QPushButton('确定')
        ok.setStyleSheet('QPushButton { background:#0078d4; color:white; border:none; border-radius:6px; padding:6px 18px; } QPushButton:hover { background:#106ebe; }')
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.content_layout.addLayout(row)
        self.setMinimumSize(460, 420)

    def _on_item_clicked(self, item):
        if self._armed_item is item:
            self._accept()
            return
        self._armed_item = item
        self.list.setCurrentItem(item)

    def _accept(self):
        item = self.list.currentItem()
        self.value = item.data(Qt.UserRole) if item else None
        if self.value is not None:
            self.accept()


