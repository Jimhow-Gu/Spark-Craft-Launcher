"""ConfigManager numeric/NaN validation tests."""

from __future__ import annotations

import math
import tempfile
import unittest
from unittest.mock import patch

from core.config import ConfigManager


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            'os.environ', {'SCL_CONFIG_DIR': self._tmp.name}, clear=False
        )
        self._env.start()
        ConfigManager._instance = None
        self.config = ConfigManager()

    def tearDown(self):
        ConfigManager._instance = None
        self._env.stop()
        self._tmp.cleanup()

    def test_memory_pair_is_clamped(self):
        self.config.set('memory_max', '4G', auto_save=False)
        self.config.set('memory_min', '1G', auto_save=False)
        self.assertEqual(self.config.get('memory_min'), '1G')
        self.config.set('memory_min', '8G', auto_save=False)
        self.assertEqual(self.config.get('memory_min'), '4G')
        self.assertEqual(self.config.get('memory_max'), '4G')

    def test_invalid_memory_rejected(self):
        self.config.set('memory_max', '4G', auto_save=False)
        self.assertFalse(self.config.set('memory_max', 'not-memory'))
        self.assertEqual(self.config.get('memory_max'), '4G')

    def test_threads_and_nan(self):
        self.assertFalse(self.config.set('download_threads', float('nan')))
        self.assertTrue(self.config.set('download_threads', 0))
        self.assertEqual(self.config.get('download_threads'), 1)
        self.config.set('download_threads', 64, auto_save=False)
        self.config.set('download_threads', 500, auto_save=False)
        self.assertEqual(self.config.get('download_threads'), 100)
        self.assertFalse(
            self.config.set('download_speed_limit', float('nan'))
        )
        self.assertFalse(self.config.set('download_speed_limit', -1))
        self.assertEqual(self.config.get('download_speed_limit'), 0.0)

    def test_volume_bounds(self):
        self.config.set('background_music_volume', -5, auto_save=False)
        self.assertEqual(self.config.get('background_music_volume'), 0)
        self.config.set('background_video_volume', 500, auto_save=False)
        self.assertEqual(self.config.get('background_video_volume'), 100)

    def test_valid_memory_text(self):
        self.assertEqual(ConfigManager._valid_memory_text('6G'), '6G')
        self.assertEqual(ConfigManager._valid_memory_text('1536'), '1536M')
        self.assertIsNone(ConfigManager._valid_memory_text('0'))
        self.assertEqual(ConfigManager._valid_memory_text('99999G'), '20G')


if __name__ == '__main__':
    unittest.main()
