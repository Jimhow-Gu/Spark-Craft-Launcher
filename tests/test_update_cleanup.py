"""Update cleanup must terminate .old holders before deleting every round."""

from __future__ import annotations

import builtins
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.update as update


class UpdateCleanupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.target = self.root / 'Spark Craft Launcher.exe'
        self.target.write_bytes(b'MZold')

    def tearDown(self):
        self._tmp.cleanup()

    def test_terminates_before_delete(self):
        old = Path(str(self.target) + '.old')
        old.write_bytes(b'MZstale')
        calls = []

        def fake_terminate(paths):
            paths = list(paths)
            if paths:
                calls.append([path.name for path in paths])
                self.assertTrue(all(path.exists() for path in paths))

        with patch.object(update.sys, 'frozen', True, create=True), \
             patch.object(update.sys, 'executable', str(self.target)), \
             patch.object(update, '_terminate_old_processes', side_effect=fake_terminate):
            remaining = update.cleanup_old_executable(retries=1)

        self.assertEqual(remaining, [])
        self.assertFalse(old.exists())
        self.assertEqual(calls, [[old.name]])

    @unittest.skipUnless(os.name == 'nt', 'taskkill is Windows-only')
    def test_taskkill_fallback_without_psutil(self):
        old = Path(str(self.target) + '.old')
        old.write_bytes(b'MZstale')
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == 'psutil':
                raise ModuleNotFoundError("No module named 'psutil'")
            return real_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=fake_import), \
             patch.object(update.subprocess, 'run') as run, \
             patch.object(update, 'write_update_log'):
            update._terminate_old_processes([old])

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ['taskkill', '/F', '/IM'])
        self.assertEqual(command[3], old.name)


if __name__ == '__main__':
    unittest.main()
