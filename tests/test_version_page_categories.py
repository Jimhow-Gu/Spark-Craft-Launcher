"""Version download category list UI tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from unittest.mock import patch
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
        texts = page.available_browser.version_ids()
        self.assertEqual(len(texts), 6)
        self.assertEqual(texts[:2], ['26.2', '26.3-pre-1'])
        self.assertIn('26.1', texts)
        self.assertIn('24w14potato', texts)
        self.assertIn('1.0', texts)
        self.assertTrue(
            page.available_browser.subtitle_at(0).startswith(
                '最新正式版，发布于 '
            )
        )
        self.assertTrue(
            page.available_browser.subtitle_at(1).startswith(
                '最新预览版，发布于 '
            )
        )

        page._set_available_filter('fool')
        self.app.processEvents()
        texts = page.available_browser.version_ids()
        self.assertIn('24w14potato', texts)
        potato_index = texts.index('24w14potato')
        potato_text = page.available_browser.subtitle_at(potato_index)
        self.assertIn('2024 |', potato_text)
        self.assertIn('毒马铃薯', potato_text)
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


    def test_modrinth_manifest_fallback(self):
        from ui.pages import version_page as page_module

        rows = [
            {'version': '1.20.1', 'version_type': 'release',
             'date': '2023-06-12T00:00:00Z'},
            {'version': '26.4-pre-1', 'version_type': 'snapshot',
             'date': '2026-11-24T00:00:00Z'},
        ]
        rows.extend(
            {'version': f'1.0.{i}', 'version_type': 'release',
             'date': f'2020-01-{(i % 28) + 1:02d}T00:00:00Z'}
            for i in range(210)
        )

        class FakeHttp:
            def __init__(self, *args, **kwargs):
                pass

            def get_json(self, *args, **kwargs):
                return rows

            def close(self):
                pass

        worker = page_module.LoadVersionsWorker([], 'all')
        with patch.object(page_module, 'HttpClient', FakeHttp):
            manifest = worker._fetch_modrinth_manifest()
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest['latest']['release'], '1.20.1')
        self.assertEqual(
            manifest['latest']['snapshot'], '26.4-pre-1'
        )
        self.assertEqual(len(manifest['versions']), len(rows))


if __name__ == '__main__':
    unittest.main()
