"""Graphics-failure window detection tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

import core.launch as launch


class _FakeProcess:
    def __init__(self):
        self.killed = False

    def poll(self):
        return None

    def kill(self):
        self.killed = True


class LaunchGraphicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_graphics_error_does_not_report_window_ready(self):
        worker = launch.LaunchWorker(
            ['--version', 'test'], '.'
        )
        worker._graphics_error_seen = True
        worker._graphics_error_message = 'No context is current'
        proc = _FakeProcess()
        worker._process = proc
        fatal = []
        ready = []
        worker.launch_fatal.connect(fatal.append)
        worker.window_ready.connect(lambda: ready.append(True))
        handles = iter((1, 0))
        with (
            patch.object(launch, '_game_window_handle', lambda _pid: next(handles, 0)),
            patch.object(launch, '_process_tree_pids', lambda pid: {pid}),
            patch.object(launch, '_reveal_hidden_game_window', lambda _pid: None),
            patch.object(launch.time, 'sleep', lambda _seconds: None),
        ):
            worker._post_launch_window_loop(12345, proc)
        self.assertEqual(ready, [])
        self.assertEqual(len(fatal), 1)
        self.assertIn('No context is current', fatal[0])
        self.assertTrue(proc.killed)


if __name__ == '__main__':
    unittest.main()
