"""Modpack import supports new and existing instance targets."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from core.modpack import ModpackImportWorker
import core.modpack as modpack
from core.net import DownloadError
from ui.pages.modpack_import_page import ModpackImportPage
from ui.version_meta import display_name as version_display_name


def _info():
    return SimpleNamespace(
        name='Test Pack',
        loader_id='fabric',
        loader_version='0.16.0',
        mc_version='1.20.1',
        file_count=3,
    )


class ModpackImportOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_existing_instance_signal(self):
        page = ModpackImportPage(
            _info(), 'pack.mrpack', 'New Pack',
            installed_versions=[
                {'id': '1.20.1-fabric', 'loader': 'fabric'},
                {'id': '1.20.1-forge', 'loader': 'forge'},
            ],
        )
        captured = []
        page.start_requested.connect(
            lambda mode, value: captured.append((mode, value))
        )
        page.existing_radio.setChecked(True)
        page.existing_combo.setCurrentIndex(1)
        page._on_start()
        self.assertEqual(captured, [('existing', '1.20.1-forge')])
        page.deleteLater()

    def test_new_instance_signal(self):
        page = ModpackImportPage(_info(), 'pack.mrpack', 'New Pack')
        self.assertFalse(page.existing_radio.isEnabled())
        captured = []
        page.start_requested.connect(
            lambda mode, value: captured.append((mode, value))
        )
        page._on_start()
        self.assertEqual(captured, [('new', 'New Pack')])
        page.deleteLater()



    def test_existing_instance_import_end_to_end(self):
        class FakeConfig:
            def __init__(self):
                self.data = {}

            def get(self, key, default=None):
                return self.data.get(key, default)

            def set(self, key, value):
                self.data[key] = value

            def save(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'versions' / '1.20.1-fabric'
            target.mkdir(parents=True)
            (target / '1.20.1-fabric.json').write_text(
                '{"clientVersion": "1.20.1"}', encoding='utf-8'
            )
            (target / 'keep.jar').write_bytes(b'keep')
            pack = root / 'pack.mrpack'
            manifest = {
                'formatVersion': 1,
                'game': 'minecraft',
                'versionId': '1.0',
                'name': 'Test Pack',
                'files': [],
                'dependencies': {
                    'minecraft': '1.20.1',
                    'fabric-loader': '0.16.0',
                },
            }
            with zipfile.ZipFile(pack, 'w') as archive:
                archive.writestr(
                    'modrinth.index.json', json.dumps(manifest)
                )
            with patch.object(modpack, 'ConfigManager', FakeConfig):
                worker = ModpackImportWorker(
                    str(pack), str(root), 'ignored',
                    existing_version='1.20.1-fabric',
                )
                ok, errors = [], []
                worker.finished_ok.connect(ok.append)
                worker.finished_error.connect(errors.append)
                worker.run()
            self.assertEqual(ok, ['1.20.1-fabric'])
            self.assertEqual(errors, [])
            self.assertTrue((target / 'keep.jar').is_file())
            self.assertTrue((target / 'pmgl_pack.json').is_file())

            class Config:
                def get(self, key, default=None):
                    return default

                def get_mc_dir(self):
                    return str(root)

            self.assertEqual(
                version_display_name(Config(), '1.20.1-fabric'),
                'Test Pack',
            )

    def test_existing_target_loader_and_version_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'versions' / '1.20.1-fabric'
            target.mkdir(parents=True)
            (target / '1.20.1-fabric.json').write_text(
                '{"clientVersion": "1.20.1"}', encoding='utf-8'
            )
            worker = ModpackImportWorker.__new__(ModpackImportWorker)
            worker._verify_existing_target(_info(), target, '1.20.1-fabric')
            bad = _info()
            bad.mc_version = '1.21.1'
            with self.assertRaises(DownloadError):
                worker._verify_existing_target(bad, target, '1.20.1-fabric')

    def test_existing_cleanup_never_deletes_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'existing'
            target.mkdir()
            (target / 'keep.jar').write_bytes(b'keep')
            worker = ModpackImportWorker.__new__(ModpackImportWorker)
            worker._created_target = False
            worker.existing_version = 'existing'
            worker._before_dirs = set()
            worker._cleanup_failed(target)
            self.assertTrue((target / 'keep.jar').is_file())


if __name__ == '__main__':
    unittest.main()
