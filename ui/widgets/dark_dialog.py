'''无边框圆角深色消息弹窗。'''

from __future__ import annotations
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (QColor, QIcon, QPainterPath)
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)
from core.app_font import ui_font

class _DialogHeader(QWidget):
    def __init__(self, dialog, title, parent=None, title_color=None):
        super().__init__(parent)
        self._dialog = dialog
        self._drag = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 8, 4)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setFont(ui_font(12))
        color = title_color or '#f0f0f0'
        label.setStyleSheet(f'color:{color}; font-weight:900; background:transparent;')
        layout.addWidget(label)
        layout.addStretch(1)
        close = QPushButton('✕')
        close.setFixedSize(30, 26)
        close.setToolTip('关闭')
        close.setStyleSheet('QPushButton { background:transparent; color:#d7d7d7; border:none; border-radius:6px; } QPushButton:hover { background:#c42b1c; color:white; }')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)

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


class DarkMessageDialog(QDialog):
    COLORS = {'error': '#c42b1c', 'warning': '#d98b00', 'info': '#0078d4'}
    MARKS = {'error': '×', 'warning': '!', 'info': 'i'}
    def __init__(self, parent, title, text, level='error'):
        super().__init__(parent)
        level = level if level in self.COLORS else 'info'
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowIcon(QIcon())
        self.setModal(True)
        self.setWindowModality(Qt.WindowModal if parent is not None else Qt.ApplicationModal)
        self.setMinimumWidth(360)
        self.setMaximumWidth(620)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        card = QFrame()
        card.setObjectName('DarkDialogCard')
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet('\n            QFrame#DarkDialogCard { background:#202020; border:1px solid rgba(255,255,255,0.14);\n                                    border-radius:12px; }\n            QLabel { background:transparent; }\n        ')
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 16)
        card_layout.setSpacing(8)
        card_layout.addWidget(_DialogHeader(self, title, card))
        body = QHBoxLayout()
        body.setContentsMargins(18, 6, 18, 4)
        body.setSpacing(14)
        icon = QLabel(self.MARKS[level])
        icon.setFixedSize(46, 46)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f'background:{self.COLORS[level]}; color:white; border-radius:23px; font-size:25px; font-weight:900;')
        body.addWidget(icon, 0, Qt.AlignTop)
        message = QLabel(str(text or ''))
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        message.setStyleSheet('color:#e8e8e8; font-size:13px;')
        message.setMinimumWidth(240)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(360)
        scroll.setStyleSheet('QScrollArea { background:transparent; border:none; } QScrollArea > QWidget > QWidget { background:transparent; }')
        scroll.viewport().setStyleSheet('background:transparent;')
        holder = QWidget()
        holder.setStyleSheet('background:transparent;')
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(message)
        holder_layout.addStretch(1)
        scroll.setWidget(holder)
        body.addWidget(scroll, 1)
        card_layout.addLayout(body)
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.window().frameGeometry().center()
        else:
            center = self.screen().availableGeometry().center()
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)


def install_dark_message_boxes():
    '''普通提示改为底部通知条，严重错误仍可显式使用 show_severe_error。'''

    from PySide6.QtWidgets import QMessageBox
    from ui.widgets.toast import (ToastManager, show_toast, toast_severity_for_title)
    from core.net import is_network_error_text
    from ui.widgets.toast import notify_network_failure
    ToastManager.instance()
    def _factory(level):
        def _show(parent, title, text, *args, **kwargs):
            blob = f'{title} {text}'
            if level in ('error', 'warning') and is_network_error_text(blob):
                if '登录' in blob:
                    notify_network_failure(parent, '验证登录', '当前网络问题导致无法登录，请使用加速器/VPN后重试！')
                    return QMessageBox.Ok
                if any((word in blob for word in ('下载', '安装', '导入', '获取', '加载'))):
                    notify_network_failure(parent, '下载文件')
                    return QMessageBox.Ok
                notify_network_failure(parent, '获取内容')
                return QMessageBox.Ok
            severity = toast_severity_for_title(title, level, str(text or ''))
            if show_toast(text, severity, parent):
                return QMessageBox.Ok
            dialog = DarkMessageDialog(parent, str(title), str(text), level)
            dialog.exec()
            return QMessageBox.Ok

        return staticmethod(_show)

    QMessageBox.critical = _factory('error')
    QMessageBox.warning = _factory('warning')
    QMessageBox.information = _factory('info')
    QMessageBox.question = staticmethod(ask_dark_question)

def show_severe_error(parent, title, text):
    '''严重启动崩溃/无法启动时保留居中弹窗。'''

    DarkMessageDialog(parent, str(title), str(text), 'error').exec()

class DarkQuestionDialog(QDialog):
    '''深色圆角确认框，支持 Yes/No/OK/Cancel 或自定义按钮。'''

    ''
    ''
    def __init__(self, parent, title, text, buttons=None, actions=None, icon_text='?', icon_color='#0078d4', title_color=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QMessageBox
        self.choice = None if actions else QMessageBox.No
        self._buttons = 0 if actions else (QMessageBox.Yes | QMessageBox.No if buttons is None else buttons)
        self._actions = list(actions) if actions else None
        self._focus_btn = None
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowIcon(QIcon())
        self.setModal(True)
        self.setWindowModality(Qt.WindowModal if parent is not None else Qt.ApplicationModal)
        self.setMinimumWidth(360)
        self.setMaximumWidth(560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        card = QFrame()
        card.setObjectName('DarkQuestionCard')
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet('\n            QFrame#DarkQuestionCard { background:#202020;\n                border:1px solid rgba(255,255,255,0.14); border-radius:12px; }\n            QLabel { background:transparent; }\n        ')
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 16)
        card_layout.setSpacing(8)
        card_layout.addWidget(_DialogHeader(self, title, card, title_color))
        body = QHBoxLayout()
        body.setContentsMargins(18, 6, 18, 8)
        body.setSpacing(14)
        icon = QLabel(str(icon_text))
        icon.setFixedSize(46, 46)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f'background:{icon_color}; color:white; border-radius:23px; font-size:25px; font-weight:900;')
        body.addWidget(icon, 0, Qt.AlignTop)
        message = QLabel(str(text or ''))
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        message.setStyleSheet('color:#e8e8e8; font-size:13px;')
        body.addWidget(message, 1)
        card_layout.addLayout(body)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(18, 4, 18, 0)
        button_row.addStretch(1)
        from PySide6.QtWidgets import QPushButton
        if self._actions:
            for label, value, primary in self._actions:
                button = self._make_button(str(label), value, primary=bool(primary))
                button_row.addWidget(button)
                if primary:
                    self._focus_btn = button
        if self._buttons & QMessageBox.Yes:
            button_row.addWidget(self._make_button('确定', QMessageBox.Yes, primary=True))
        if self._buttons & QMessageBox.Ok:
            button_row.addWidget(self._make_button('确定', QMessageBox.Ok, primary=True))
        if self._buttons & QMessageBox.No:
            self._focus_btn = self._make_button('取消', QMessageBox.No)
            button_row.addWidget(self._focus_btn)
        if self._buttons & QMessageBox.Cancel:
            self._focus_btn = self._make_button('取消', QMessageBox.Cancel)
            button_row.addWidget(self._focus_btn)
        card_layout.addLayout(button_row)
        if self._focus_btn is not None:
            self._focus_btn.setDefault(True)
            self._focus_btn.setFocus()
        self.adjustSize()

    def _make_button(self, text, value, primary=False):
        from PySide6.QtWidgets import QPushButton
        button = QPushButton(text)
        button.setMinimumWidth(88)
        button.setMinimumHeight(32)
        button.setStyleSheet('QPushButton { background:#0078d4; color:white; border:none; border-radius:6px; }QPushButton:hover { background:#106ebe; }' if primary else 'QPushButton { background:#3a3a3a; color:#f0f0f0; border:none; border-radius:6px; }QPushButton:hover { background:#4a4a4a; }')
        button.clicked.connect((lambda: self._choose(value)))
        return button

    def _choose(self, value):
        self.choice = value
        self.accept()

    def reject(self):
        from PySide6.QtWidgets import QMessageBox
        if self._actions:
            if self.choice is None:
                self.choice = 'cancel'
        elif not self.choice or self.choice == QMessageBox.No:
            self.choice = QMessageBox.No
        super().reject()

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.window().frameGeometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)


def ask_dark_question(parent, title, text, buttons=None, default_button=None):
    from PySide6.QtWidgets import QMessageBox
    dialog = DarkQuestionDialog(parent, str(title), str(text), buttons)
    dialog.exec()
    if dialog.choice is not None:
        return dialog.choice
    return QMessageBox.No

