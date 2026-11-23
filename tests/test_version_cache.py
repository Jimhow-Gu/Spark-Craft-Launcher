"""Version list cache and single-pass JSON scan tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigManager
from core.version import VersionManager, invalidate_versions_cache


class VersionCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mc = self.root / 'mc'
        self.versions = self.mc / 'versions'
        self.versions.mkdir(parents=True)
        self._env = patch.dict(
            'os.environ', {'SCL_CONFIG_DIR': str(self.root / 'scl')},
            clear=False,
        )
        self._env.start()
        ConfigManager._instance = None
        invalidate_versions_cache()
        self._write('A', {
            'id': 'A', 'type': 'release', 'mainClass': 'Main',
        })
        self._write('B', {
            'id': 'B', 'inheritsFrom': 'A', 'type': 'release',
            'mainClass': 'Main',
            'libraries': [{'name': 'net.fabricmc:fabric-loader:0.16.0'}],
        })
        self.mgr = VersionManager(str(self.mc))

    def tearDown(self):
        invalidate_versions_cache()
        ConfigManager._instance = None
        self._env.stop()
        self._tmp.cleanup()

    def _write(self, name, data):
        folder = self.versions / name
        folder.mkdir()
        (folder / f'{name}.json').write_text(
            json.dumps(data), encoding='utf-8'
        )

    def _count_reads(self):
        calls = []
        original = VersionManager._read_version_json

        def counted(path):
            calls.append(path)
            return original(path)

        return calls, patch.object(
            VersionManager, '_read_version_json', staticmethod(counted)
        )

    def test_cache_hit_does_not_reread_json(self):
        calls, patcher = self._count_reads()
        with patcher:
            first = self.mgr.get_installed_versions()
            second = self.mgr.get_installed_versions()
        self.assertEqual(len(calls), 2)
        self.assertEqual([item['id'] for item in first], ['B'])
        self.assertEqual(first, second)
        self.assertTrue(first[0]['modded'])
        self.assertEqual(first[0]['loader'], 'fabric')

    def test_invalidate_forces_rescan(self):
        calls, patcher = self._count_reads()
        with patcher:
            self.mgr.get_installed_versions()
            invalidate_versions_cache(self.versions)
            self.mgr.get_installed_versions()
        self.assertEqual(len(calls), 4)

    def test_delete_invalidates_and_reveals_parent(self):
        self.mgr.get_installed_versions()
        self.assertTrue(self.mgr.delete_version('B'))
        versions = self.mgr.get_installed_versions()
        self.assertEqual([item['id'] for item in versions], ['A'])
        self.assertFalse(versions[0]['modded'])


if __name__ == '__main__':
    unittest.main()
