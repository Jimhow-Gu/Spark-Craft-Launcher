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

    def test_failed_notice_is_consumed(self):
        update._failed_update_marker('1.11.4', 'copy failed')
        notice = update.consume_failed_update_notice()
        self.assertIn('自动更新失败', notice)
        self.assertIn('copy failed', notice)
        self.assertEqual(update.consume_failed_update_notice(), '')

    def test_new_exe_helper_replaces_target(self):
        source = self.root / 'downloaded-new.exe'
        source.write_bytes(b'MZ' + b'new-build')
        target = self.root / 'Spark Craft Launcher.exe'
        target.write_bytes(b'MZ' + b'old-build')
        with patch.object(update.sys, 'frozen', True, create=True), \
             patch.object(update.sys, 'executable', str(source)):
            code = update.run_update_helper([
                'launcher', '--apply-update', str(target), '1.11.4', '0', '0',
            ])
        self.assertEqual(code, 0)
        self.assertEqual(target.read_bytes(), source.read_bytes())
        updated = self._path('updated_update.json')
        self.assertTrue(updated.is_file())
        data = json.loads(updated.read_text(encoding='utf-8'))
        self.assertEqual(data['state'], 'updated')

    def test_launcher_passes_target_and_pids(self):
        target = self.root / 'Spark Craft Launcher.exe'
        target.write_bytes(b'MZ')
        new_exe = self.root / 'update-new.exe'
        new_exe.write_bytes(b'MZ')
        with patch.object(update.sys, 'frozen', True, create=True), \
             patch.object(update.sys, 'executable', str(target)), \
             patch.object(update.os, 'getpid', return_value=111), \
             patch.object(update.os, 'getppid', return_value=222), \
             patch.object(update, '_extract_exe', return_value=new_exe), \
             patch.object(update.subprocess, 'Popen') as popen:
            result = update.launch_update_helper(Path('asset.exe'), '1.11.4')
        self.assertEqual(result, new_exe)
        command = popen.call_args.args[0]
        self.assertIn('--apply-update', command)
        self.assertIn(str(target), command)
        self.assertIn('1.11.4', command)
        self.assertIn('111', command)
        self.assertIn('222', command)

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
        self.assertIn('Wait-Or-Kill $WaitPid 45', text)
        self.assertIn('Wait-Or-Kill $ParentPid 15', text)
        self.assertIn('parent path check failed', text)
        self.assertIn('Stop-BackupProcess', text)
        self.assertIn('cleanup exe backup', text)
        self.assertIn('exit 2', text)
        args = popen.call_args.args[0]
        self.assertIn(str(update._updated_update_path()), args)
        self.assertIn(str(update._update_marker_path('failed_update.json')), args)


if __name__ == '__main__':
    unittest.main()
