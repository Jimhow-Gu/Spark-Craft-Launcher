"""Windows taskbar progress priority/state tests."""

from __future__ import annotations

import ctypes
import sys
import unittest
from unittest.mock import patch

from core.taskbar_progress import TaskbarProgress


@unittest.skipUnless(sys.platform == 'win32', 'Windows taskbar only')
class TaskbarProgressTests(unittest.TestCase):
    def setUp(self):
        self.progress = TaskbarProgress()
        self.progress._hwnd = 1
        self.progress._ptr = ctypes.c_void_p(1)
        self.states = []
        self.values = []
        self._state_patch = patch.object(
            self.progress, '_set_state',
            side_effect=lambda state: self.states.append(state),
        )
        self._value_patch = patch.object(
            self.progress, '_set_value',
            side_effect=lambda done, total: self.values.append((done, total)),
        )
        self._state_patch.start()
        self._value_patch.start()

    def tearDown(self):
        self._state_patch.stop()
        self._value_patch.stop()
        self.progress._ptr = None

    def test_normal_and_priority(self):
        self.progress.set_task('download', 55)
        self.assertEqual(self.states[-1], 2)
        self.assertEqual(self.values[-1], (5500, 10000))
        self.progress.set_task('launch', None)
        self.assertEqual(self.states[-1], 1)
        self.progress.clear_task('launch')
        self.assertEqual(self.states[-1], 2)
        self.assertEqual(self.values[-1], (5500, 10000))

    def test_sync_download(self):
        self.progress.sync_download({
            'active': True, 'failed': False, 'percent': 73,
        })
        self.assertEqual(self.states[-1], 2)
        self.assertEqual(self.values[-1], (7300, 10000))
        self.progress.sync_download({'active': False})
        self.assertEqual(self.states[-1], 0)

    def test_error_state(self):
        self.progress.sync_download({
            'active': True, 'failed': True, 'percent': 20,
        })
        self.assertEqual(self.states[-1], 4)


if __name__ == '__main__':
    unittest.main()
