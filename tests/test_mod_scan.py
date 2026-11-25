"""Nested/JiJ mod counting tests."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.mod_scan import scan_mod_ids
from core.memory import count_mods


class ModScanTests(unittest.TestCase):
    def test_nested_fabric_modules_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / 'mods'
            mods.mkdir()
            nested = io.BytesIO()
            with zipfile.ZipFile(nested, 'w') as zf:
                zf.writestr(
                    'fabric.mod.json',
                    json.dumps({'id': 'nested-mod', 'version': '1.0'}),
                )
            with zipfile.ZipFile(mods / 'outer.jar', 'w') as zf:
                zf.writestr(
                    'fabric.mod.json',
                    json.dumps({'id': 'outer-mod', 'version': '1.0'}),
                )
                zf.writestr('META-INF/jars/nested.jar', nested.getvalue())
            self.assertEqual(
                scan_mod_ids(mods), {'outermod', 'nestedmod'}
            )
            self.assertEqual(count_mods(str(root)), 2)

    def test_recursive_mods_and_filename_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            nested_dir = mods / 'optional'
            nested_dir.mkdir()
            with zipfile.ZipFile(nested_dir / 'plain.jar', 'w') as zf:
                zf.writestr('readme.txt', 'no metadata')
            self.assertEqual(scan_mod_ids(mods), {'plain'})


if __name__ == '__main__':
    unittest.main()
