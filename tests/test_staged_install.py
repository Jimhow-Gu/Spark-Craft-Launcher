"""Failure-path tests for staged downloads, installers and Java runtime."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import core.installers as installers
import core.java_download as java_download
import core.staged as staged
from core.net import HttpClient


def make_zip(files=None):
    files = files or {'a.txt': b'ok'}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


class FakeResponse:
    text = ''

    def __init__(self, data=b'', status=200, headers=None, content=None):
        self.data = data
        self.status_code = status
        self.headers = headers or {}
        self.reason = 'test'
        self._content = content if content is not None else data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, *args, **kwargs):
        if self._content:
            yield self._content


class FakeHttp:
    def __init__(self, payloads, sha1=''):
        self.payloads = payloads
        self.sha1 = sha1
        self.calls = []

    def get(self, url, **kwargs):
        response = FakeResponse()
        response.text = self.sha1
        return response

    def download(self, url, dest, **kwargs):
        self.calls.append(url)
        payload = self.payloads.get(url)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            raise RuntimeError('no payload')
        Path(dest).write_bytes(payload)

    def close(self):
        pass


class StagedDownloadTests(unittest.TestCase):
    def test_html_then_valid_zip_keeps_old_until_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'installer.jar'
            dest.write_bytes(b'OLD')
            http = FakeHttp({'bad': b'<html>200</html>', 'good': make_zip()})
            installers._download_installer(['bad', 'good'], dest, http=http)
            self.assertTrue(dest.read_bytes().startswith(b'PK'))
            self.assertEqual(http.calls, ['bad', 'good'])

    def test_all_installer_candidates_fail_keeps_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'installer.jar'
            dest.write_bytes(b'OLD')
            http = FakeHttp({'bad': b'html', 'bad2': b'broken'})
            with self.assertRaises(installers.DownloadError):
                installers._download_installer(['bad', 'bad2'], dest, http=http)
            self.assertEqual(dest.read_bytes(), b'OLD')
            self.assertFalse((Path(tmp) / 'installer.jar.new').exists())

    def test_target_locked_keeps_temp_and_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'installer.jar'
            dest.write_bytes(b'OLD')
            http = FakeHttp({'good': make_zip()})
            with patch('core.staged.os.replace', side_effect=PermissionError('locked')):
                with self.assertRaises(staged.StagedDownloadError):
                    staged.staged_download(
                        ['good'], dest, http=http, check_zip=True,
                        error_label='安装器下载',
                    )
            self.assertEqual(dest.read_bytes(), b'OLD')
            self.assertTrue((Path(tmp) / 'installer.jar.new').exists())

    def test_installer_sha1_mismatch_keeps_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'installer.jar'
            dest.write_bytes(b'OLD')
            expected = hashlib.sha1(make_zip()).hexdigest()
            url = 'https://maven.minecraftforge.net/x.jar'
            http = FakeHttp({url: b'wrong'}, sha1=expected)
            with self.assertRaises(installers.DownloadError):
                installers._download_installer([url], dest, http=http)
            self.assertEqual(dest.read_bytes(), b'OLD')

    def test_empty_staged_file_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'file.bin'
            dest.write_bytes(b'OLD')
            (Path(tmp) / 'file.bin.new').write_bytes(b'')
            http = FakeHttp({'good': b'NEW'})
            staged.staged_download(['good'], dest, http=http)
            self.assertEqual(dest.read_bytes(), b'NEW')
            self.assertEqual(http.calls, ['good'])

    def test_empty_staged_file_can_be_opted_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'file.bin'
            dest.write_bytes(b'OLD')
            (Path(tmp) / 'file.bin.new').write_bytes(b'')
            staged.staged_download([], dest, allow_empty=True)
            self.assertEqual(dest.read_bytes(), b'')

    def test_all_hashes_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file.bin'
            path.write_bytes(b'content')
            sha1 = hashlib.sha1(b'content').hexdigest()
            sha256 = hashlib.sha256(b'content').hexdigest()
            sha512 = hashlib.sha512(b'content').hexdigest()
            self.assertTrue(HttpClient._verify(
                path, sha1=sha1, sha256=sha256, sha512=sha512, size=7
            ))
            self.assertFalse(HttpClient._verify(
                path, sha1=sha1, sha256='0' * 64, size=7
            ))
            self.assertFalse(HttpClient._verify(
                path, sha256=sha256, sha512='0' * 128, size=7
            ))

    def test_no_cleanroom_mirror(self):
        text = Path(installers.__file__).read_text(encoding='utf-8')
        self.assertNotIn('cleanroommc', text.lower())


class JavaRuntimeTests(unittest.TestCase):
    def test_bad_java_zip_keeps_existing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'runtime' / 'Java-17'
            target.mkdir(parents=True)
            (target / 'OLD').write_text('old', encoding='utf-8')
            with patch.object(java_download, 'scl_dir', return_value=root), \
                 patch.object(java_download, '_find_installed', return_value=None), \
                 patch.object(java_download, '_discover_candidates', return_value=[
                     ('test', 'http://x/java.zip', 'java.zip', '', 0)
                 ]), \
                 patch.object(java_download, 'HttpClient', return_value=FakeHttp({
                     'http://x/java.zip': b'bad'
                 })):
                with self.assertRaises(java_download.DownloadError):
                    java_download.install_java_runtime(17)
            self.assertEqual((target / 'OLD').read_text(encoding='utf-8'), 'old')

    def test_good_java_zip_swaps_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'runtime' / 'Java-17'
            target.mkdir(parents=True)
            (target / 'OLD').write_text('old', encoding='utf-8')
            payload = make_zip({
                'bin/javaw.exe': b'x',
                'release': b'JAVA_VERSION="17"',
            })
            with patch.object(java_download, 'scl_dir', return_value=root), \
                 patch.object(java_download, '_find_installed', return_value=None), \
                 patch.object(java_download, '_discover_candidates', return_value=[
                     ('test', 'http://x/java.zip', 'java.zip', '', 0)
                 ]), \
                 patch.object(java_download, 'java_major_of', return_value=17), \
                 patch.object(java_download, 'HttpClient', return_value=FakeHttp({
                     'http://x/java.zip': payload
                 })):
                exe = java_download.install_java_runtime(17)
            self.assertTrue(exe.is_file())
            self.assertFalse((target / 'OLD').exists())
            self.assertTrue(list((root / 'runtime').glob('.Java-17.old-*')))

    def test_locked_runtime_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'Java-17'
            target.mkdir()
            (target / 'OLD').write_text('old', encoding='utf-8')
            staging = root / '.Java-17.new'
            staging.mkdir()
            (staging / 'NEW').write_text('new', encoding='utf-8')
            original = Path.rename

            def fail_target_rename(self, dst):
                if self == target:
                    raise PermissionError('locked')
                return original(self, dst)

            with patch.object(Path, 'rename', fail_target_rename):
                with self.assertRaises(java_download.DownloadError):
                    java_download._replace_runtime(target, staging)
            self.assertEqual((target / 'OLD').read_text(encoding='utf-8'), 'old')


class SidecarTests(unittest.TestCase):
    def _client(self, response):
        class Session:
            def __init__(self):
                self.headers = None

            def get(self, *args, **kwargs):
                self.headers = kwargs.get('headers')
                return response

        client = HttpClient(timeout=1, retries=1, verify=False)
        client.session = Session()
        return client

    def test_etag_change_discards_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'file.bin'
            part = dest.with_name(dest.name + '.part')
            meta = part.with_name(part.name + '.meta')
            part.write_bytes(b'hello')
            meta.write_text(json.dumps({
                'url': 'http://x', 'etag': 'old', 'total_size': 10,
            }), encoding='utf-8')
            response = FakeResponse(
                b'world', status=206,
                headers={
                    'Content-Range': 'bytes 5-9/10',
                    'ETag': 'new',
                    'Content-Length': '5',
                },
            )
            with self.assertRaises(Exception):
                self._client(response)._download_stream('http://x', dest, size=10)
            self.assertFalse(part.exists())
            self.assertFalse(meta.exists())

    def test_no_validator_disables_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'file.bin'
            part = dest.with_name(dest.name + '.part')
            meta = part.with_name(part.name + '.meta')
            part.write_bytes(b'xx')
            meta.write_text(
                json.dumps({'url': 'http://x', 'total_size': 3}),
                encoding='utf-8',
            )
            response = FakeResponse(
                b'abc', status=200, headers={'Content-Length': '3'}
            )
            client = self._client(response)
            client._download_stream('http://x', dest, size=3)
            self.assertFalse(client.session.headers)
            self.assertEqual(dest.read_bytes(), b'abc')


if __name__ == '__main__':
    unittest.main()
