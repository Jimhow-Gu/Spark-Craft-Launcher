"""PCL-style Java requirement matrix tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.java_requirements import java_requirement_bounds
from core.installers import pick_java


class JavaRequirementTests(unittest.TestCase):
    def check(self, mc, loader, loader_version, expected, **kwargs):
        self.assertEqual(
            java_requirement_bounds(
                mc, loader=loader, loader_version=loader_version, **kwargs
            ),
            expected,
        )

    def test_forge_matrix(self):
        self.check('1.12.2', 'forge', '14.23.5.2860', (8, 8))
        self.check('1.16.5', 'forge', '36.2.34', (8, 23))
        self.check('1.16.5', 'forge', '36.2.25', (8, 8))
        self.check('1.17.1', 'forge', '37.0.79', (16, 16))
        self.check('1.19.4', 'forge', '45.0.21', (17, 19))
        self.check('1.19.4', 'forge', '45.0.66', (17, 21))
        self.check(
            '1.18.2', 'forge', '40.2.10', (17, 18),
            has_optifine=True,
        )
        self.check('1.6.4', 'forge', '9.11.1.1345', (7, 7))

    def test_neoforge_and_fabric(self):
        self.check('1.20.1', 'neoforge', '47.1.5', (17, 21))
        self.check('1.20.2', 'neoforge', '20.2.61-beta', (17, 21))
        self.check('1.20.5', 'fabric', '0.16.0', (21, 24))
        self.check('1.20.1', 'fabric', '0.16.0', (17, 24))
        self.check('1.20.5', 'fabric', '0.17.0', (21, None))

    def test_optifine_and_json_java(self):
        self.check('1.8.9', 'optifine', '', (8, 8))
        self.check('1.12.2', 'optifine', '', (8, 8))
        self.check('26.1', '', '', (25, None))
        self.check(
            '26.1', '', '', (25, None), java_major=25
        )

    def test_installer_picker_uses_loader_bounds(self):
        calls = []

        class Manager:
            def _select_best_java(self, required, maximum=None):
                calls.append((required, maximum))
                return ('1.8.0_402', 'C:/Java8/bin/java.exe')

        with patch('core.launch.LaunchManager', Manager):
            path = pick_java(
                '1.12.2', loader='forge',
                loader_version='14.23.5.2860',
            )
        self.assertTrue(path.endswith('java.exe'))
        self.assertEqual(calls, [(8, 8)])


if __name__ == '__main__':
    unittest.main()
