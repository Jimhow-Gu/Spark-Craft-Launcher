"""Shared animated list component tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QWidget

from ui.widgets.animated_list import (
    AnimatedListContainer, AnimatedListView, AnimatedPopupCombo,
)


class AnimatedListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_list_has_framed_rounded_style(self):
        widget = AnimatedListView()
        style = widget.styleSheet()
        self.assertIn('border-radius: 12px', style)
        self.assertIn('QListWidget::item:selected', style)
        widget.deleteLater()

    def test_container_expand_collapse_without_animation(self):
        content = QWidget()
        content.setMinimumHeight(100)
        container = AnimatedListContainer(content)
        container.show()
        container.set_expanded(False, animate=False)
        self.assertFalse(container.is_expanded())
        self.assertFalse(content.isVisible())
        container.set_expanded(True, animate=False)
        self.assertTrue(container.is_expanded())
        self.assertTrue(content.isVisible())
        container.close()
        container.deleteLater()

    def test_popup_combo_subclasses_rounded_combo(self):
        combo = AnimatedPopupCombo()
        combo.addItems(['A', 'B'])
        self.assertEqual(combo.count(), 2)
        combo.deleteLater()


if __name__ == '__main__':
    unittest.main()
