"""Version manifest merge and source candidate tests."""

from __future__ import annotations

import unittest

from core.version_sources import (
    BMCLAPI,
    MIN_VERSION_COUNT,
    PISTON,
    V1_PATH,
    V2_PATH,
    manifest_candidates,
    merge_manifests,
)


class VersionSourceTests(unittest.TestCase):
    def test_candidates_v1_then_v2(self):
        v1 = manifest_candidates()
        self.assertIn(PISTON + V1_PATH, v1)
        self.assertIn(BMCLAPI + V1_PATH, v1)
        self.assertNotIn(V2_PATH, ' '.join(v1))
        v2 = manifest_candidates(include_v2=True)
        self.assertTrue(any(url.endswith(V2_PATH) for url in v2))

    def test_merge_official_priority_and_missing_fields(self):
        official = {
            'latest': {'release': 'old', 'snapshot': 'old-snap'},
            'versions': [
                {
                    'id': '1.0', 'type': 'release',
                    'releaseTime': '2025-01-01T00:00:00Z',
                    'url': 'official',
                },
                {
                    'id': '26.4-snapshot-10', 'type': 'snapshot',
                    'releaseTime': '2026-01-01T00:00:00Z',
                    'url': 'official',
                },
            ],
        }
        mirror = {
            'latest': {'release': '1.0', 'snapshot': '26.4-snapshot-10'},
            'versions': [
                {
                    'id': '1.0', 'type': 'release',
                    'releaseTime': '2025-01-01T00:00:00Z',
                    'url': 'mirror', 'mirror_only': 'x',
                },
                {
                    'id': '0.9', 'type': 'release',
                    'releaseTime': '2024-01-01T00:00:00Z',
                    'url': 'mirror',
                },
            ],
        }
        merged = merge_manifests(
            [official, mirror],
            [PISTON + V1_PATH, BMCLAPI + V1_PATH],
        )
        by_id = {v['id']: v for v in merged['versions']}
        self.assertEqual(by_id['1.0']['url'], 'official')
        self.assertEqual(by_id['1.0']['mirror_only'], 'x')
        self.assertIn('0.9', by_id)
        self.assertEqual(merged['latest']['release'], '1.0')
        self.assertEqual(
            merged['latest']['snapshot'], '26.4-snapshot-10'
        )
        self.assertGreaterEqual(MIN_VERSION_COUNT, 200)

    def test_min_versions_filters_small_manifest(self):
        merged = merge_manifests(
            [{'versions': [{'id': 'x', 'type': 'release'}]}],
            [PISTON + V1_PATH],
            min_versions=MIN_VERSION_COUNT,
        )
        self.assertEqual(merged['versions'], [])


if __name__ == '__main__':
    unittest.main()
