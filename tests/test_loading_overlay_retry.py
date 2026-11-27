"""Loading overlay failure text and click-to-retry tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ui.widgets.loading_overlay import LoadingOverlay


class LoadingOverlayRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_failure_shows_exclamation_and_click_retries(self):
        host = QWidget()
        host.resize(400, 300)
        overlay = LoadingOverlay(host)
        overlay.attach_to(host)
        host.show()
        overlay.fail('测试失败')
        self.app.processEvents()
        self.assertEqual(overlay.status_label.text(), '加载失败！')
        clicked = []
        overlay.retry_requested.connect(lambda: clicked.append(True))
        QTest.mouseClick(overlay, Qt.LeftButton)
        self.assertEqual(clicked, [True])
        host.close()
        host.deleteLater()


if __name__ == '__main__':
    unittest.main()
