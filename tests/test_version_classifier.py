"""Version category classifier tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from core.version_classifier import (
    category_counts, classify_version, grouped_versions,
)
from core.version_lore import fool_registry
from ui.version_meta import ICON_FILES


def v(version_id, version_type='release', release_time='2026-01-01T00:00:00Z'):
    return {
        'id': version_id, 'type': version_type,
        'releaseTime': release_time,
    }


class VersionClassifierTests(unittest.TestCase):
    def test_core_categories(self):
        self.assertEqual(classify_version(v('26.1')), 'release')
        self.assertEqual(
            classify_version(v('26.3-pre-1', 'snapshot')), 'preview'
        )
        self.assertEqual(
            classify_version(v('26.3-rc-1', 'snapshot')), 'preview'
        )
        self.assertEqual(
            classify_version(v('26.3-snapshot-1', 'snapshot')), 'snapshot'
        )
        self.assertEqual(
            classify_version(v('24w14potato', 'snapshot')), 'fool'
        )
        self.assertEqual(
            classify_version(v('15w14a', 'snapshot')), 'fool'
        )
        self.assertEqual(
            classify_version(v('1.0', 'release', '2011-11-18T00:00:00Z')),
            'old',
        )
        self.assertEqual(
            classify_version(v('a1.2.6', 'old_alpha', '2010-01-01T00:00:00Z')),
            'old',
        )

    def test_april_first_snapshot_is_fool(self):
        self.assertEqual(
            classify_version(
                v('26w14a', 'snapshot', '2026-04-01T00:00:00Z')
            ),
            'fool',
        )
        self.assertTrue(fool_registry.is_fool_id('26w14a'))

    def test_unknown_type_falls_back_to_snapshot(self):
        self.assertEqual(classify_version(v('mystery', 'unknown')), 'snapshot')

    def test_preview_and_special_icons_exist(self):
        self.assertEqual(ICON_FILES['preview'], 'CommandBlock.png')
        for name in (
            ICON_FILES['preview'], ICON_FILES['fool'], ICON_FILES['old'],
        ):
            self.assertTrue(
                (Path('ui/imgs') / name).is_file(), name
            )

    def test_counts_and_group_order(self):
        versions = [
            v('26.1'), v('26.2'),
            v('26.3-pre-1', 'snapshot'),
            v('26.3-snapshot-1', 'snapshot'),
            v('24w14potato', 'snapshot'),
            v('1.0', 'release', '2011-11-18T00:00:00Z'),
        ]
        counts = category_counts(versions)
        self.assertEqual(counts['release'], 2)
        self.assertEqual(counts['preview'], 1)
        self.assertEqual(counts['snapshot'], 1)
        self.assertEqual(counts['fool'], 1)
        self.assertEqual(counts['old'], 1)
        groups = grouped_versions(versions)
        self.assertEqual(
            list(groups), ['release', 'snapshot', 'preview', 'fool', 'old']
        )


if __name__ == '__main__':
    unittest.main()
