"""Version download category list UI tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QLabel

from ui.pages.version_page import VersionPage


VERSIONS = [
    {'id': '26.1', 'type': 'release', 'releaseTime': '2026-01-01T00:00:00Z'},
    {'id': '26.2', 'type': 'release', 'releaseTime': '2026-02-01T00:00:00Z'},
    {'id': '26.3-pre-1', 'type': 'snapshot', 'releaseTime': '2026-03-01T00:00:00Z'},
    {'id': '26.3-snapshot-1', 'type': 'snapshot', 'releaseTime': '2026-02-15T00:00:00Z'},
    {'id': '24w14potato', 'type': 'snapshot', 'releaseTime': '2024-04-01T00:00:00Z'},
    {'id': '1.0', 'type': 'release', 'releaseTime': '2011-11-18T00:00:00Z'},
]


class VersionPageCategoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_default_all_and_filtered_lists(self):
        page = VersionPage()
        page.resize(1100, 700)
        page.show()
        page._render_available_versions(VERSIONS)
        self.app.processEvents()
        self.assertEqual(page.available_category_combo.count(), 6)
        self.assertEqual(
            page.available_category_combo.currentData(), 'all'
        )
        self.assertEqual(
            page.available_category_combo.currentText(), '全部'
        )
        labels = [
            label.text()
            for label in page.findChildren(QLabel)
        ]
        self.assertIn('版本类别', labels)
        headers = []
        for index in range(page.available_list.count()):
            holder = page.available_list.itemWidget(
                page.available_list.item(index)
            )
            if holder is not None:
                headers.extend(
                    label.text() for label in holder.findChildren(QLabel)
                )
        self.assertIn('最新版本', headers)
        self.assertIn('正式版', headers)
        self.assertIn('愚人节版', headers)
        self.assertIn('远古版', headers)
        self.assertNotIn('正式版（2）', headers)

        page._set_available_filter('fool')
        self.app.processEvents()
        texts = [
            page.available_list.item(index).text()
            for index in range(page.available_list.count())
        ]
        self.assertIn('24w14potato', texts)
        page.close()
        page.deleteLater()

    def test_install_steps_include_optifine_and_optifabric_phases(self):
        page = VersionPage()
        captures = []
        page._current_detail_version_id = '1.20.1'
        page._current_detail_page = None
        page._select_section = lambda _section: None
        page._suspend_available_refresh = lambda: None
        page._active_install_jobs = lambda: []
        page._start_install_job = captures.append
        page._on_download_requested('1.20.1-test', {
            'fabric': '0.16.0',
            'optifine': 'HD_U_I6',
            'optifabric': {'version': '1.14.0'},
            'fabric_api': '0.92.0',
        })
        job = captures[0]
        self.assertEqual(
            job['steps'][5:],
            [
                '安装游戏', '下载游戏支持库文件',
                '下载 OptiFine', '安装 OptiFine',
                '安装 OptiFabric', '下载 Fabric API',
            ],
        )
        phases = job['phase_steps']
        for key in (
            'loader_file', 'loader_install', 'game', 'game_libs',
            'optifine_file', 'optifine_install', 'optifabric',
            'fabric_api',
        ):
            self.assertIn(key, phases)
            self.assertLess(phases[key], len(job['steps']))
        page.close()
        page.deleteLater()


if __name__ == '__main__':
    unittest.main()
