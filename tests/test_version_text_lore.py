"""Version date and lore registry tests."""

from __future__ import annotations

import unittest

from core.version_lore import FoolLoreRegistry, validate_payload
from core.version_text import (
    format_release_time, is_april_first, parse_release_datetime,
    release_year,
)


def version(version_id, release_time, version_type='snapshot'):
    return {
        'id': version_id,
        'type': version_type,
        'releaseTime': release_time,
    }


class VersionTextTests(unittest.TestCase):
    def test_format_and_precedence(self):
        item = {
            'id': '26.3',
            'releaseTime': '2026-09-15T11:23:00Z',
            'time': '2020-01-01T00:00:00Z',
        }
        parsed = parse_release_datetime(item)
        self.assertIsNotNone(parsed)
        self.assertEqual(
            format_release_time(item), parsed.strftime('%Y/%m/%d %H:%M')
        )
        self.assertEqual(release_year(item), '2026')
        self.assertEqual(
            format_release_time({'id': 'x'}), '发布日期未知'
        )

    def test_april_first(self):
        self.assertTrue(
            is_april_first(version('26w14a', '2026-04-01T00:00:00Z'))
        )
        self.assertFalse(
            is_april_first(version('26w14a', '2026-04-02T00:00:00Z'))
        )


class FoolLoreTests(unittest.TestCase):
    def test_seed_and_alias_lookup(self):
        registry = FoolLoreRegistry()
        self.assertTrue(registry.is_fool_id('24w14potato'))
        self.assertTrue(registry.is_fool_id('20w14∞'))
        self.assertTrue(registry.is_fool_id('1.RV-Pre1'))
        text = registry.lore_text(
            version('24w14potato', '2024-04-01T00:00:00Z')
        )
        self.assertTrue(text.startswith('2024 | '))
        self.assertIn('毒马铃薯', text)

    def test_validation_rejects_bad_schema_and_truncates(self):
        self.assertIsNone(validate_payload({'schema': 2, 'entries': []}))
        payload = validate_payload({
            'schema': 1,
            'entries': [
                {'id': ' X ', 'text': 'a' * 400},
                {'id': '', 'text': 'bad'},
            ],
        })
        self.assertIsNotNone(payload)
        self.assertEqual(payload['entries'][0]['id'], 'x')
        self.assertEqual(len(payload['entries'][0]['text']), 300)

    def test_unknown_fool_falls_back_in_ui_layer(self):
        registry = FoolLoreRegistry()
        registry._loaded = True
        registry._texts = {}
        registry._ids = set()
        text = registry.lore_text(
            version('27w14a', '2027-04-01T00:00:00Z')
        )
        self.assertEqual(text, '')


if __name__ == '__main__':
    unittest.main()
