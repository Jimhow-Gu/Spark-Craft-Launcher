"""Repair quick-validation and missing-file corruption checks."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.downloading import RemoteFile
from core.version import VersionManager


def make_zip():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as zf:
        zf.writestr('a.txt', b'ok')
    return stream.getvalue()


class MissingModeValidationTests(unittest.TestCase):
    def test_known_size_mismatch_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lib.jar'
            path.write_bytes(make_zip())
            task = RemoteFile(
                label='lib', dest=path, urls=['x'],
                size=path.stat().st_size + 10, check_zip=True,
            )
            self.assertFalse(task.valid('missing'))

    def test_corrupt_jar_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lib.jar'
            path.write_bytes(b'not a zip')
            task = RemoteFile(
                label='lib', dest=path, urls=['x'],
                size=path.stat().st_size, check_zip=True,
            )
            self.assertFalse(task.valid('missing'))

    def test_valid_jar_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lib.jar'
            data = make_zip()
            path.write_bytes(data)
            task = RemoteFile(
                label='lib', dest=path, urls=['x'],
                size=len(data), check_zip=True,
            )
            self.assertTrue(task.valid('missing'))


class QuickVersionValidationTests(unittest.TestCase):
    def test_quick_validation_skips_library_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc = Path(tmp) / 'mc'
            versions = mc / 'versions'
            ver = versions / 'Test'
            ver.mkdir(parents=True)
            (ver / 'Test.json').write_text(json.dumps({
                'id': 'Test',
                'type': 'release',
                'mainClass': 'Main',
                'libraries': [{
                    'name': 'org.example:missing:1.0',
                    'downloads': {'artifact': {
                        'path': 'org/example/missing/1.0/missing-1.0.jar',
                    }},
                }],
            }), encoding='utf-8')
            manager = VersionManager(str(mc))
            quick = manager.validate_version('Test', quick=True)
            full = manager.validate_version('Test', quick=False)
            self.assertNotIn(
                '缺少 1 个依赖库', quick.get('issues') or []
            )
            self.assertIn(
                '缺少 1 个依赖库', full.get('issues') or []
            )


if __name__ == '__main__':
    unittest.main()
