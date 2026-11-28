"""Version detail loader row visibility/error tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import ui.pages.version_detail_page as detail
from ui.pages.version_detail_page import VersionDetailPage


class _Worker(QObject):
    loaded = Signal(dict)
    partial = Signal(dict)
    partial_errors = Signal(dict)
    finished = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__()

    def start(self):
        pass

    def cancel(self):
        pass

    def isRunning(self):
        return False


class VersionDetailLoaderUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_empty_loader_rows_hide_and_error_rows_show(self):
        with patch.object(detail, 'LoadVersionsWorker', _Worker):
            page = VersionDetailPage('1.20.1')
        page.resize(900, 700)
        page.show()
        self.app.processEvents()
        page._loader_versions = {
            'forge': [], 'neoforge': [], 'fabric': ['0.16.0'],
            'fabric_api': [], 'optifine': [], 'optifabric': [],
        }
        page._loaded_loader_ids = set(page._loader_versions)
        page._loader_errors = {'forge': 'offline'}
        page._update_loader_ui()
        self.assertFalse(page.loader_widgets['forge'].isHidden())
        self.assertEqual(page._loader_status_labels['forge'].text(), '加载失败！')
        self.assertTrue(page.loader_widgets['optifine'].isHidden())
        self.assertTrue(page.loader_widgets['optifabric'].isHidden())
        page.close()
        page.deleteLater()

    def test_loader_version_rows_use_readable_font(self):
        with patch.object(detail, 'LoadVersionsWorker', _Worker):
            page = VersionDetailPage('1.20.1')
        page._loader_versions = {
            'forge': ['66.1.9', '66.1.8', '66.1.5'],
            'neoforge': [], 'fabric': [],
            'fabric_api': [], 'optifine': [], 'optifabric': [],
        }
        page._loaded_loader_ids = set(page._loader_versions)
        page._update_loader_ui()
        body = page._loader_version_lists['forge']
        self.assertEqual(body.count(), 3)
        self.assertEqual(body.item(0).font().pointSize(), 12)
        self.assertEqual(body.iconSize().width(), 18)
        self.assertEqual(body.iconSize().height(), 18)
        page.close()
        page.deleteLater()

    def test_timed_out_loader_row_shows_retry_text(self):
        with patch.object(detail, 'LoadVersionsWorker', _Worker):
            page = VersionDetailPage('1.20.1')
        page.resize(900, 700)
        page.show()
        self.app.processEvents()
        page._loader_versions = {
            'forge': [], 'neoforge': [], 'fabric': [],
            'fabric_api': [], 'optifine': [], 'optifabric': [],
        }
        page._loaded_loader_ids = set(page._loader_versions)
        page._timed_out_loader_keys = {'forge', 'optifine'}
        page._update_loader_ui()
        self.assertFalse(page.loader_widgets['forge'].isHidden())
        self.assertEqual(
            page._loader_status_labels['forge'].text(),
            '加载超时，点击重试',
        )
        self.assertFalse(page.loader_widgets['optifine'].isHidden())
        self.assertEqual(
            page._loader_status_labels['optifine'].text(),
            '加载超时，点击重试',
        )
        page.close()
        page.deleteLater()


if __name__ == '__main__':
    unittest.main()
