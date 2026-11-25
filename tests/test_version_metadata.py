"""Version metadata multi-source tests (no network)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import core.downloading as downloading


class _Response:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


class _OfficialClient:
    def __init__(self, *args, **kwargs):
        pass

    def get(self, url, timeout=None):
        if 'version_manifest' in url:
            return _Response({
                'versions': [{
                    'id': '26.3',
                    'type': 'release',
                    'url': 'https://piston-meta.mojang.com/mc/game/26.3.json',
                }]
            })
        return _Response({'id': '26.3', 'type': 'release'})

    def close(self):
        pass


class _FallbackClient:
    def __init__(self, *args, **kwargs):
        pass

    def get(self, url, timeout=None):
        if 'piston-meta' in url or 'launchermeta' in url:
            raise TimeoutError('blocked')
        if 'version_manifest' in url:
            return _Response({
                'versions': [{
                    'id': '26.3',
                    'type': 'release',
                    'url': 'https://bmclapi2.bangbang93.com/version/26.3/json',
                }]
            })
        return _Response({'id': '26.3', 'type': 'release'})

    def close(self):
        pass


class VersionMetadataTests(unittest.TestCase):
    def test_manifest_urls_include_v1_and_v2(self):
        urls = downloading.version_manifest_urls('auto')
        self.assertTrue(any('launchermeta.mojang.com/mc/game/version_manifest.json' in u for u in urls))
        self.assertTrue(any('version_manifest_v2.json' in u for u in urls))

    def test_official_source_does_not_request_mirrors(self):
        urls = downloading.version_manifest_urls('official')
        self.assertTrue(urls)
        self.assertFalse(any('bmclapi' in url for url in urls))
        self.assertFalse(any('openbmclapi' in url for url in urls))

    def test_bmcl_source_prefers_mirror(self):
        urls = downloading.version_manifest_urls('bmclapi')
        self.assertTrue(urls[0].startswith('https://bmclapi2'))

    def test_official_source_has_no_direct_mirror_json(self):
        self.assertEqual(downloading.version_json_urls('26.3', 'official'), [])

    def test_official_manifest_entry(self):
        with patch.object(downloading, 'HttpClient', _OfficialClient):
            data = downloading.fetch_version_json('26.3', http=_OfficialClient())
        self.assertEqual(data['id'], '26.3')

    def test_falls_back_to_bmcl_when_official_blocked(self):
        with patch.object(downloading, 'HttpClient', _FallbackClient):
            data = downloading.fetch_version_json(
                '26.3', http=_FallbackClient(), source='auto'
            )
        self.assertEqual(data['id'], '26.3')


if __name__ == '__main__':
    unittest.main()
