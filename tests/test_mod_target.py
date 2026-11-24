"""Mod browser must keep complete snapshot versions and compare exact files."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from ui.pages.mod_browser_page import ModBrowserPage, ProjectFilesDialog


class _Config:
    def __init__(self, root, version_id):
        self.root = Path(root)
        self.version_id = version_id

    def get(self, key, default=None):
        if key == 'selected_version':
            return self.version_id
        return default

    def get_mc_dir(self):
        return str(self.root)


class ModTargetVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, root, version_id):
        dialog = ModBrowserPage.__new__(ModBrowserPage)
        dialog.config = _Config(root, version_id)
        return dialog

    def test_snapshot_version_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version_id = '26.4-snapshot-10'
            version_dir = root / 'versions' / version_id
            version_dir.mkdir(parents=True)
            (version_dir / f'{version_id}.json').write_text(
                json.dumps({'clientVersion': version_id}),
                encoding='utf-8',
            )
            dialog = self._dialog(root, version_id)
            self.assertEqual(dialog._target_mc_version_from_instance(), version_id)

    def test_loader_suffix_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version_id = '1.20.1-forge-47.2.0'
            version_dir = root / 'versions' / version_id
            version_dir.mkdir(parents=True)
            (version_dir / f'{version_id}.json').write_text(
                json.dumps({'clientVersion': '1.20.1'}),
                encoding='utf-8',
            )
            dialog = self._dialog(root, version_id)
            self.assertEqual(dialog._target_mc_version_from_instance(), '1.20.1')

    def test_snapshot_suffix_stays_when_loader_is_stripped(self):
        self.assertEqual(
            ModBrowserPage._strip_loader_suffix(
                '26.4-snapshot-10-fabric'
            ),
            '26.4-snapshot-10',
        )

    def test_file_compatibility_is_exact(self):
        dialog = ProjectFilesDialog.__new__(ProjectFilesDialog)
        dialog._files_mc_version = '26.4-snapshot-10'
        exact = SimpleNamespace(game_versions=['26.4-snapshot-10'])
        base = SimpleNamespace(game_versions=['26.4'])
        self.assertTrue(dialog._file_supports_target(exact))
        self.assertFalse(dialog._file_supports_target(base))


if __name__ == '__main__':
    unittest.main()
