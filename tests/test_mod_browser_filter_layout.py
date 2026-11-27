"""Resource center filter layout tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from ui.pages.mod_browser_page import ModBrowserPage
from ui.widgets.rounded_select import RoundedSelect


class ModBrowserFilterLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_search_first_row_and_second_row_stretches(self):
        page = ModBrowserPage('mod')
        page.resize(1200, 760)
        page.show()
        self.app.processEvents()
        search = page.search_input.geometry()
        source = page.source_combo.geometry()
        sort = page.sort_combo.geometry()
        self.assertEqual(search.y(), source.y())
        self.assertEqual(source.y(), sort.y())
        self.assertLess(search.x(), source.x())
        self.assertLess(source.x(), sort.x())
        self.assertGreater(search.width(), 700)
        for combo in (
            page.source_combo, page.sort_combo,
            page.category_combo, page.name_match_combo,
        ):
            self.assertIsInstance(combo, RoundedSelect)
        second_y = page.mc_combo.geometry().y()
        for combo in (
            page.category_combo, page.name_match_combo,
        ):
            self.assertEqual(combo.geometry().y(), second_y)
        self.assertGreater(
            page.mc_combo.width(), page.category_combo.width()
        )
        self.assertFalse(hasattr(page, 'loader_combo'))
        buttons = {
            button.text(): button
            for button in page.findChildren(QPushButton)
        }
        reset = buttons['重置条件']
        self.assertLessEqual(
            abs(reset.geometry().right() - reset.parent().width() + 10),
            8,
        )

        page.resize(780, 760)
        self.app.processEvents()
        self.assertEqual(
            page.search_input.geometry().y(),
            page.source_combo.geometry().y(),
        )
        self.assertGreater(page.search_input.width(), 180)
        for combo in (
            page.mc_combo, page.category_combo, page.name_match_combo,
        ):
            self.assertEqual(combo.geometry().y(), second_y)
        page.close()
        page.deleteLater()

    def test_version_combo_uses_custom_popup(self):
        page = ModBrowserPage('mod')
        page.resize(1000, 700)
        page.show()
        self.app.processEvents()
        combo = page.mc_combo
        combo.addItem('1.20.1', '1.20.1')
        combo.showPopup()
        QTest.qWait(220)
        self.assertTrue(combo._custom_popup.isVisible())
        view = combo._custom_popup.list
        rect = view.visualRect(view.model().index(1, 0))
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=rect.center())
        QTest.qWait(120)
        self.assertEqual(combo.currentText(), '1.20.1')
        self.assertFalse(combo._custom_popup.isVisible())
        page.close()
        page.deleteLater()

    def test_modpack_install_button_stays_on_first_row(self):
        page = ModBrowserPage('modpack')
        page.resize(1000, 760)
        page.show()
        self.app.processEvents()
        buttons = [
            button for button in page.findChildren(QPushButton)
            if button.text() == '安装已有整合包'
        ]
        self.assertEqual(len(buttons), 1)
        button = buttons[0]
        search = page.search_input.geometry()
        self.assertEqual(search.y(), button.geometry().y())
        self.assertGreater(button.geometry().x(), search.x() + search.width())
        page.close()
        page.deleteLater()


if __name__ == '__main__':
    unittest.main()
