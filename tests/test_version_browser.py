"""Version browser virtual-list tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.widgets.version_browser import ROW_HEIGHT, VersionBrowserView


def row_info(version, category):
    return f"{version['id']}-{category}"


class VersionBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_items_are_two_line_46px(self):
        view = VersionBrowserView()
        view.set_data(
            flat_items=[({'id': '26.3'}, 'release')],
            row_info=lambda _version, _category: 'date',
            icon_provider=lambda _version, _category: None,
            all_mode=False,
        )
        self.assertEqual(view.count(), 1)
        self.assertEqual(view.item(0).sizeHint().height(), ROW_HEIGHT)
        self.assertEqual(view.version_ids(), ['26.3'])
        self.assertEqual(view.subtitle_at(0), 'date')

    def test_all_mode_is_flat_list(self):
        view = VersionBrowserView()
        latest = [({'id': '26.3'}, 'release')]
        sections = [
            ('release', '正式版', [({'id': '26.1'}, 'release')]),
            ('fool', '愚人节版', [({'id': '24w14potato'}, 'fool')]),
        ]
        view.set_data(
            latest_items=latest,
            sections=sections,
            row_info=row_info,
            icon_provider=lambda _version, _category: None,
            all_mode=True,
        )
        self.assertEqual(
            view.version_ids(),
            ['26.3', '26.1', '24w14potato'],
        )
        self.assertFalse(view.is_empty())
        view.set_data(
            flat_items=[({'id': '24w14potato'}, 'fool')],
            row_info=row_info,
            icon_provider=lambda _version, _category: None,
            all_mode=False,
        )
        self.assertEqual(view.version_ids(), ['24w14potato'])
        self.assertEqual(view.subtitle_at(0), '24w14potato-fool')
        view.close()

    def test_double_click_emits_version(self):
        view = VersionBrowserView()
        view.resize(400, 200)
        view.set_data(
            flat_items=[({'id': '26.3'}, 'release')],
            row_info=lambda _version, _category: 'date',
            icon_provider=lambda _version, _category: None,
            all_mode=False,
        )
        view.show()
        self.app.processEvents()
        seen = []
        view.version_activated.connect(seen.append)
        item = view.item(0)
        QTest.mouseDClick(
            view.viewport(), Qt.LeftButton,
            pos=view.visualItemRect(item).center(),
        )
        self.assertEqual(seen, ['26.3'])


if __name__ == '__main__':
    unittest.main()
