"""Modpack icon and Mod completeness tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

import core.modpack as modpack
from core.modpack import ModpackImportWorker
from core.net import DownloadError
from ui.version_meta import version_icon


def _png_bytes():
    pix = QPixmap(16, 16)
    pix.fill(QColor('red'))
    fd, name = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    path = Path(name)
    try:
        pix.save(str(path), 'PNG')
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


class ModpackAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_extracts_jpg_icon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / 'pack.zip'
            # _is_image_bytes only needs a JPEG signature here.
            with zipfile.ZipFile(pack, 'w') as zf:
                zf.writestr('icon.jpg', b'\xff\xd8\xff\xe0test')
            worker = ModpackImportWorker(str(pack), str(root))
            info = SimpleNamespace(
                icon_prefixes=[''], extra={},
            )
            alias = root / 'versions' / 'test'
            alias.mkdir(parents=True)
            self.assertEqual(worker._extract_pack_icon(info, alias), 'icon.png')
            self.assertTrue((alias / 'icon.png').is_file())

    def test_multimc_icon_key_is_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / 'pack.zip'
            with zipfile.ZipFile(pack, 'w') as zf:
                zf.writestr(
                    'mmc-pack.json',
                    json.dumps({'components': [
                        {'uid': 'net.minecraft', 'version': '1.20.1'}
                    ]}),
                )
                zf.writestr('instance.cfg', 'name=Test\niconKey=myicon\n')
            with zipfile.ZipFile(pack) as zf:
                info = modpack._inspect_multimc(
                    pack, zf, 'mmc-pack.json', zf.namelist()
                )
            self.assertEqual(info.extra.get('icon_key'), 'myicon')

    def test_version_icon_reads_pmgl_pack_icon_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version_dir = root / 'versions' / 'test'
            version_dir.mkdir(parents=True)
            (version_dir / 'custom.png').write_bytes(_png_bytes())
            (version_dir / 'pmgl_pack.json').write_text(
                json.dumps({'icon': 'custom.png'}), encoding='utf-8'
            )

            class Config:
                def get(self, key, default=None):
                    return default

                def get_mc_dir(self):
                    return str(root)

            self.assertEqual(
                version_icon(root, 'test', Config()),
                version_dir / 'custom.png',
            )

    def test_remote_icon_download(self):
        class Response:
            content = _png_bytes()

            def raise_for_status(self):
                pass

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url):
                return Response()

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alias = root / 'versions' / 'test'
            alias.mkdir(parents=True)
            worker = ModpackImportWorker(
                str(root / 'pack.zip'), str(root),
                icon_url='https://example.invalid/cover.png',
            )
            with patch.object(modpack, 'HttpClient', Client):
                self.assertEqual(
                    worker._download_remote_icon(alias), 'icon.png'
                )
            self.assertTrue((alias / 'icon.png').is_file())

    def test_required_mod_missing_fails_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alias = root / 'versions' / 'test'
            alias.mkdir(parents=True)
            worker = ModpackImportWorker(str(root / 'pack.zip'), str(root))
            records = [{
                'path': 'mods/required.jar',
                'required': True,
            }]
            with self.assertRaises(DownloadError):
                worker._verify_imported_mods(alias, records)
            (alias / 'mods').mkdir()
            (alias / 'mods' / 'required.jar').write_bytes(b'jar')
            worker._verify_imported_mods(alias, records)


if __name__ == '__main__':
    unittest.main()
