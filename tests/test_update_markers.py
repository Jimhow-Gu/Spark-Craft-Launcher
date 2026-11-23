"""Update success receipts must never be written before installation succeeds."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.update as update


class UpdateMarkerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch = patch.object(update, 'scl_dir', return_value=self.root)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _path(self, name):
        return self.root / 'update' / name

    def test_installing_is_not_success(self):
        update._pending_update_marker('1.11.4')
        self.assertFalse(update.pending_update_exists())
        self.assertEqual(update.consume_updated_marker(), '')
        self.assertTrue(self._path('pending_update.json').is_file())
        data = json.loads(
            self._path('pending_update.json').read_text(encoding='utf-8')
        )
        self.assertEqual(data['state'], 'installing')

    def test_updated_is_consumed(self):
        update._pending_update_marker('1.11.4')
        update._updated_update_marker('1.11.4')
        self.assertTrue(update.pending_update_exists())
        self.assertEqual(update.consume_updated_marker(), '1.11.4')
        self.assertFalse(self._path('updated_update.json').exists())
        self.assertFalse(self._path('pending_update.json').exists())
        self.assertFalse(self._path('failed_update.json').exists())

    def test_failed_is_not_success(self):
        update._pending_update_marker('1.11.4')
        update._failed_update_marker('1.11.4', 'copy failed')
        self.assertFalse(update.pending_update_exists())
        self.assertEqual(update.consume_updated_marker(), '')
        self.assertTrue(self._path('failed_update.json').is_file())

    def test_onedir_script_has_success_and_failure_receipts(self):
        target = self.root / 'Spark Craft Launcher.exe'
        target.write_bytes(b'MZ')
        new_root = self.root / 'new'
        (new_root / '_internal').mkdir(parents=True)
        (new_root / '_internal' / 'x.dll').write_bytes(b'x')
        new_exe = new_root / target.name
        new_exe.write_bytes(b'MZ')
        with patch.object(update.sys, 'frozen', True, create=True), \
             patch.object(update.sys, 'executable', str(target)), \
             patch.object(
                 update, '_extract_onedir', return_value=(new_exe, new_root)
             ), \
             patch.object(update.subprocess, 'Popen') as popen:
            script = update._prepare_onedir_update(
                Path('update.zip'), '1.11.4'
            )
        text = script.read_text(encoding='utf-8-sig')
        self.assertIn('Write-UpdateMarker $UpdatedMarker "updated"', text)
        self.assertIn('Fail "copy failed after rollback"', text)
        self.assertIn('Get-FileHash', text)
        self.assertIn('trap {', text)
        args = popen.call_args.args[0]
        self.assertIn(str(update._updated_update_path()), args)
        self.assertIn(str(update._update_marker_path('failed_update.json')), args)


if __name__ == '__main__':
    unittest.main()
