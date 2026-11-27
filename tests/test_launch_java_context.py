"""LaunchManager Java context tests with a temporary version tree."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.launch as launch


class _Config:
    def __init__(self, root):
        self.root = str(root)

    def get(self, key, default=None):
        return default

    def get_mc_dir(self):
        return self.root


class LaunchJavaContextTests(unittest.TestCase):
    def test_forge_version_and_json_java_flow_into_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'versions' / '1.16.5'
            base.mkdir(parents=True)
            (base / '1.16.5.json').write_text(json.dumps({
                'id': '1.16.5',
                'type': 'release',
                'releaseTime': '2020-01-01T00:00:00Z',
                'javaVersion': {'majorVersion': 8},
            }), encoding='utf-8')
            child = root / 'versions' / 'forge-test'
            child.mkdir()
            (child / 'forge-test.json').write_text(json.dumps({
                'id': 'forge-test',
                'inheritsFrom': '1.16.5',
                'libraries': [{
                    'name': 'net.minecraftforge:forge:1.16.5-36.2.34',
                }],
            }), encoding='utf-8')
            with patch.object(
                launch, 'ConfigManager', lambda: _Config(root)
            ):
                manager = launch.LaunchManager()
                context = manager._get_java_context('forge-test')
                required = manager._get_required_java_version('forge-test')
                bounds = manager._get_java_bounds('forge-test', required)
            self.assertEqual(context['base'], '1.16.5')
            self.assertEqual(context['loader'], 'forge')
            self.assertEqual(context['loader_version'], '36.2.34')
            self.assertEqual(bounds, (8, 23, True))

    def test_optifine_flag_caps_forge_eighteen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'versions' / '1.18.2'
            base.mkdir(parents=True)
            (base / '1.18.2.json').write_text(json.dumps({
                'id': '1.18.2',
                'type': 'release',
                'releaseTime': '2022-02-01T00:00:00Z',
                'javaVersion': {'majorVersion': 17},
            }), encoding='utf-8')
            child = root / 'versions' / 'forge-optifine'
            child.mkdir()
            (child / 'forge-optifine.json').write_text(json.dumps({
                'id': 'forge-optifine',
                'inheritsFrom': '1.18.2',
                'libraries': [
                    {'name': 'net.minecraftforge:forge:1.18.2-40.2.10'},
                    {'name': 'optifine:OptiFine:1.18.2_HD_U_H9'},
                ],
            }), encoding='utf-8')
            with patch.object(
                launch, 'ConfigManager', lambda: _Config(root)
            ):
                manager = launch.LaunchManager()
                bounds = manager._get_java_bounds(
                    'forge-optifine',
                    manager._get_required_java_version('forge-optifine'),
                )
            self.assertEqual(bounds, (17, 18, True))


if __name__ == '__main__':
    unittest.main()
