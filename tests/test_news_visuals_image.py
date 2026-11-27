"""Adaptive version-cover height tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from ui.widgets.news_visuals import NewsImageLabel


class NewsImageHeightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_wide_cover_uses_aspect_height_with_cap(self):
        pixmap = QPixmap(1170, 500)
        pixmap.fill(QColor('red'))
        label = NewsImageLabel(adaptive_height=True, max_height=420)
        label.show()
        label.resize(1000, 180)
        label.set_source(pixmap)
        self.app.processEvents()
        self.assertEqual(label.height(), 420)

        label.resize(500, 180)
        self.app.processEvents()
        self.assertEqual(label.height(), 213)

    def test_regular_news_card_keeps_180_height(self):
        pixmap = QPixmap(1170, 500)
        pixmap.fill(QColor('red'))
        label = NewsImageLabel()
        label.show()
        label.resize(1000, 180)
        label.set_source(pixmap)
        self.app.processEvents()
        self.assertEqual(label.height(), 180)


if __name__ == '__main__':
    unittest.main()
