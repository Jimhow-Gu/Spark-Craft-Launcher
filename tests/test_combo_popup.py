"""Rounded combo popup anchoring tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.widgets.rounded_combo import RoundedComboBox


class ComboPopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _wait(self, ms=180):
        loop = QTimer()
        loop.setSingleShot(True)
        loop.timeout.connect(self.app.quit)
        loop.start(ms)
        self.app.exec()

    def test_mouse_click_opens_below_and_reopens_same_place(self):
        combo = RoundedComboBox()
        combo.addItems(['A', 'B', 'C'])
        combo.move(100, 100)
        combo.resize(180, 34)
        combo.show()
        self.app.processEvents()
        QTest.mouseClick(combo, Qt.LeftButton)
        self.app.processEvents()
        popup = combo.view().window()
        self.assertTrue(popup.isVisible())
        anchor = combo.mapToGlobal(QPoint(0, combo.height()))
        self.assertLessEqual(abs(popup.x() - anchor.x()), 4)
        self.assertLessEqual(abs(popup.y() - anchor.y()), 8)
        position = popup.pos()
        combo.hidePopup()
        self._wait()
        self.assertFalse(popup.isVisible())
        combo.showPopup()
        self.app.processEvents()
        self.assertEqual(popup.pos(), position)
        combo.close()

    def test_popup_opens_on_release_not_press(self):
        combo = RoundedComboBox()
        combo.addItems(['A', 'B', 'C'])
        combo.move(100, 100)
        combo.resize(180, 34)
        combo.show()
        self.app.processEvents()
        popup = combo.view().window()
        center = combo.rect().center()
        QTest.mousePress(combo, Qt.LeftButton, pos=center)
        self.app.processEvents()
        self.assertFalse(popup.isVisible())
        QTest.mouseRelease(combo, Qt.LeftButton, pos=center)
        self.app.processEvents()
        self.assertTrue(popup.isVisible())
        combo.hidePopup()
        combo.close()

    def test_hover_index_tracks_pointer_and_clears_on_leave(self):
        combo = RoundedComboBox()
        combo.addItems(['A', 'B', 'C'])
        combo.move(100, 100)
        combo.resize(180, 34)
        combo.show()
        self.app.processEvents()
        combo.showPopup()
        self.app.processEvents()
        view = combo.view()
        viewport = view.viewport()
        for row in range(3):
            rect = view.visualRect(view.model().index(row, 0))
            QTest.mouseMove(viewport, rect.center())
            self.app.processEvents()
            self.assertEqual(combo._hover_index, row)
            self.assertEqual(view._hover_index, row)
        QTest.mouseMove(viewport, QPoint(viewport.width() - 2, viewport.height() - 2))
        self.app.processEvents()
        self.assertEqual(combo._hover_index, -1)
        self.assertEqual(view._hover_index, -1)
        combo.hidePopup()
        combo.close()

    def test_near_bottom_flips_above(self):
        combo = RoundedComboBox()
        combo.addItems([f'Item {index}' for index in range(30)])
        combo.move(100, 720)
        combo.resize(180, 34)
        combo.show()
        self.app.processEvents()
        combo.showPopup()
        self.app.processEvents()
        popup = combo.view().window()
        self.assertTrue(popup.isVisible())
        top = combo.mapToGlobal(QPoint(0, 0)).y()
        self.assertLess(popup.y(), top)
        combo.hidePopup()
        combo.close()


if __name__ == '__main__':
    unittest.main()
