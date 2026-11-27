"""Forge/NeoForge loader source fallback tests."""

from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from ui.pages.version_detail_page import LoadVersionsWorker


class LoaderFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_forge_falls_back_to_maven_metadata(self):
        worker = LoadVersionsWorker('1.16.5')

        def fake_json(url, timeout=8):
            if 'bmclapi' in url:
                raise ConnectionError('bmcl down')
            if 'promotions' in url:
                return {'promos': {'1.16.5-latest': '36.2.34'}}
            return None

        def fake_text(url, timeout=10):
            if 'maven-metadata' in url:
                return (
                    '<metadata><versioning><versions>'
                    '<version>1.16.5-36.2.34</version>'
                    '<version>1.16.5-36.2.25</version>'
                    '<version>1.15.2-31.2.0</version>'
                    '</versions></versioning></metadata>'
                )
            return None

        worker._fetch_json = fake_json
        worker._fetch_text = fake_text
        versions = worker._get_forge()
        self.assertEqual(versions[:2], ['36.2.34', '36.2.25'])
        self.assertNotIn('forge', worker.errors)

    def test_neoforge_falls_back_to_official_api(self):
        worker = LoadVersionsWorker('1.20.2')

        def fake_json(url, timeout=8):
            if 'bmclapi' in url:
                raise ConnectionError('bmcl down')
            if 'maven.neoforged.net/api' in url:
                return {
                    'versions': [
                        '20.2.62-beta', '20.2.61-beta', '20.4.1',
                    ]
                }
            return None

        worker._fetch_json = fake_json
        worker._fetch_text = lambda url, timeout=10: None
        versions = worker._get_neoforge()
        self.assertEqual(versions, ['20.2.62-beta', '20.2.61-beta'])
        self.assertNotIn('neoforge', worker.errors)

    def test_unreachable_sources_keep_error(self):
        worker = LoadVersionsWorker('1.16.5')

        def fail(url, timeout=8):
            raise ConnectionError('offline')

        worker._fetch_json = fail
        worker._fetch_text = lambda url, timeout=10: fail(url, timeout)
        self.assertEqual(worker._get_forge(), [])
        self.assertIn('forge', worker.errors)
        neoforge = LoadVersionsWorker('1.20.1')
        neoforge._local_neoforge_versions = lambda: []
        neoforge._fetch_json = fail
        neoforge._fetch_text = lambda url, timeout=10: fail(url, timeout)
        self.assertEqual(neoforge._get_neoforge(), [])
        self.assertIn('neoforge', neoforge.errors)


    def test_forge_falls_back_to_curseforge_modloader(self):
        worker = LoadVersionsWorker('26.3')

        def fake_json(url, timeout=8):
            if 'curseforge.com' in url:
                return {'data': [
                    {'name': 'forge-66.1.9', 'gameVersion': '26.3'},
                    {'name': 'forge-66.1.8', 'gameVersion': '26.3'},
                    {'name': 'forge-47.4.0', 'gameVersion': '1.20.1'},
                ]}
            raise ConnectionError('blocked')

        worker._fetch_json = fake_json
        worker._fetch_text = lambda url, timeout=10: (_ for _ in ()).throw(ConnectionError('blocked'))
        versions = worker._get_forge()
        self.assertEqual(versions[:2], ['66.1.9', '66.1.8'])
        self.assertNotIn('forge', worker.errors)

    def test_neoforge_falls_back_to_gitcode_tags(self):
        worker = LoadVersionsWorker('26.3')

        def fake_json(url, timeout=8):
            if '/projects/gh_mirrors' in url:
                return {'id': 1653786}
            if '/repository/tags' in url:
                return {
                    'content': [
                        {'name': '26.3.0-stable'},
                        {'name': '26.3.0'},
                        {'name': '21.1.264'},
                    ],
                    'page_count': 1,
                }
            raise ConnectionError('blocked')

        worker._fetch_json = fake_json
        worker._fetch_text = lambda url, timeout=10: (_ for _ in ()).throw(ConnectionError('blocked'))
        worker._local_neoforge_versions = lambda: []
        versions = worker._get_neoforge()
        self.assertEqual(versions, ['26.3.0'])
        self.assertNotIn('neoforge', worker.errors)


if __name__ == '__main__':
    unittest.main()
