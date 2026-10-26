'''可折叠设置分组：圆角卡片、标题栏与展开/收起动画。'''

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from core.app_font import ui_font
from ui.widgets.painted_icons import set_button_icon


class _CollapsibleHeader(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('CollapsibleHeader')
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CollapsibleSection(QWidget):
    toggled = Signal(bool)

    def __init__(self, title, parent=None, *, expanded=False):
        super().__init__(parent)
        self._expanded = bool(expanded)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.card = QFrame()
        self.card.setObjectName('CollapsibleCard')
        self.card.setAttribute(Qt.WA_StyledBackground, True)
        self.card.setStyleSheet(
            'QFrame#CollapsibleCard { background:rgba(18,20,26,0.58);'
            ' border:1px solid rgba(255,255,255,0.22); border-radius:12px; }'
            'QFrame#CollapsibleCard QLabel { background:transparent; border:none; }'
            'QFrame#CollapsibleHeader { background:transparent; border:none; border-radius:12px; }'
            'QFrame#CollapsibleHeader:hover { background:rgba(255,255,255,0.04); }'
        )
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        self.header = _CollapsibleHeader()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(24, 13, 16, 13)
        header_layout.setSpacing(8)
        self.title_label = QLabel(str(title))
        self.title_label.setFont(ui_font(15, 600))
        self.title_label.setStyleSheet('color:rgba(255,255,255,0.85); background:transparent;')
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        self.arrow = QToolButton()
        self.arrow.setFixedSize(28, 28)
        self.arrow.setIconSize(QSize(15, 15))
        set_button_icon(self.arrow, 'chevron_right', 15, '#aab4c0')
        self.arrow.setStyleSheet(
            'QToolButton { background:transparent; border:none; border-radius:6px; }'
            'QToolButton:hover { background:rgba(255,255,255,0.08); }'
        )
        self.arrow.setToolTip('展开')
        self.arrow.clicked.connect(self.toggle)
        header_layout.addWidget(self.arrow)
        card_layout.addWidget(self.header)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(24, 0, 24, 18)
        self.content_layout.setSpacing(10)
        card_layout.addWidget(self.content)
        outer.addWidget(self.card)
        self._animation = QPropertyAnimation(self.content, b'maximumHeight', self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.finished.connect(self._on_animation_finished)
        self.header.clicked.connect(self.toggle)
        if self._expanded:
            self.content.setMaximumHeight(16777215)
            self.content.show()
            set_button_icon(self.arrow, 'chevron_up', 15, '#aab4c0')
            self.arrow.setToolTip('收起')
        else:
            self.content.setMaximumHeight(0)
            self.content.hide()


    def set_title(self, text):
        self.title_label.setText(str(text or ''))

    def is_expanded(self):
        return self._expanded

    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded, animate=True):
        expanded = bool(expanded)
        if expanded == self._expanded and self.content.isVisible() == expanded:
            return
        self._expanded = expanded
        self._animation.stop()
        if expanded:
            self.content.setMaximumHeight(16777215)
            self.content.show()
            layout = self.content.layout()
            if layout is not None:
                layout.activate()
            self.content.adjustSize()
            target = max(1, self.content.sizeHint().height())
            if not animate or not self.isVisible():
                self.content.setMaximumHeight(16777215)
            else:
                self.content.setMaximumHeight(0)
                self._animation.setStartValue(0)
                self._animation.setEndValue(target)
                self._animation.start()
            set_button_icon(self.arrow, 'chevron_up', 15, '#aab4c0')
            self.arrow.setToolTip('收起')
        else:
            if not animate or not self.content.isVisible():
                self.content.hide()
                self.content.setMaximumHeight(0)
            else:
                start = max(1, self.content.height())
                self.content.setMaximumHeight(start)
                self._animation.setStartValue(start)
                self._animation.setEndValue(0)
                self._animation.start()
            set_button_icon(self.arrow, 'chevron_right', 15, '#aab4c0')
            self.arrow.setToolTip('展开')
        self.toggled.emit(expanded)

    def _on_animation_finished(self):
        if self._expanded:
            self.content.setMaximumHeight(16777215)
        else:
            self.content.hide()
            self.content.setMaximumHeight(0)
