'''点击当前已选项时保持下拉列表展开的组合框。'''

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox


class PersistentComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._keep_open_until = 0.0
        self._initial_index = -1
        self.view().pressed.connect(self._on_view_pressed)
        self.view().doubleClicked.connect(self._on_view_double_clicked)

    def showPopup(self):
        self._initial_index = self.currentIndex()
        super().showPopup()

    def _keep_for(self, index):
        if index.isValid() and index.row() == self._initial_index:
            self._keep_open_until = time.monotonic() + 0.6
            return True
        self._keep_open_until = 0.0
        return False

    def _on_view_pressed(self, index):
        self._keep_for(index)

    def _on_view_double_clicked(self, index):
        if not self._keep_for(index):
            super().hidePopup()

    def hidePopup(self):
        if time.monotonic() < self._keep_open_until:
            return
        self._keep_open_until = 0.0
        super().hidePopup()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._keep_open_until = 0.0
        super().keyPressEvent(event)
