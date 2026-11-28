"""News card image cache refresh/staleness tests."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication

import ui.pages.news_page as news_page
from ui.pages.news_page import NewsPage


def png_bytes(color='red'):
    image = QImage(4, 4, QImage.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    data = bytes(buffer.data())
    buffer.close()
    return data


class _Reply:
    def __init__(self, data):
        self._data = data

    def error(self):
        return QNetworkReply.NoError

    def header(self, _header):
        return len(self._data)

    def readAll(self):
        return self._data

    def deleteLater(self):
        pass


class NewsImageCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dispose(self, page):
        page.shutdown()
        page.close()
        page.deleteLater()
        self.app.processEvents()

    def test_same_signature_manual_refresh_reloads_card_images(self):
        page = NewsPage()
        text = '<local:MyCard Title="x">same</local:MyCard>'
        page._news_signature = hashlib.sha1(
            text.encode('utf-8')
        ).hexdigest()[:16]
        page._cards = [{'kind': 'version'}]
        page._refresh_manual = True
        with patch.object(page, '_refresh_card_images') as refresh, \
             patch.object(page, '_scroll_to_top'), \
             patch.object(news_page, 'show_toast'):
            page._on_news_loaded(text)
        refresh.assert_called_once()
        self.assertFalse(page._refresh_manual)
        self._dispose(page)

    def test_render_then_refresh_requests_images_again(self):
        page = NewsPage()
        calls = []
        page.request_image = lambda label, urls: calls.append(list(urls))
        page._render([{
            'kind': 'version', 'title': 'v', 'version': '1.20.1',
            'images': ['https://example.invalid/a.png'],
        }])
        self.assertEqual(len(calls), 1)
        page._refresh_card_images()
        self.assertEqual(len(calls), 2)
        self._dispose(page)

    def test_stale_reply_does_not_poison_new_signature_cache(self):
        page = NewsPage()
        data = png_bytes()
        reply = _Reply(data)
        label = news_page.NewsImageLabel()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(news_page, 'scl_dir', return_value=root), \
                 patch.object(page, '_news_signature', 'new'):
                page._image_generation = 2
                page._image_pending[reply] = (
                    label, [], 'https://example.invalid/a.png',
                    'old', 1, True,
                )
                page._on_image_finished(reply)
                cache = (
                    root / 'cache' / 'mcnews_images' / 'new'
                    / hashlib.sha1(
                        b'https://example.invalid/a.png'
                    ).hexdigest()
                )
                self.assertFalse(cache.exists())
        label.deleteLater()
        self._dispose(page)

    def test_forced_refresh_overwrites_existing_cache(self):
        page = NewsPage()
        old_data = png_bytes('blue')
        new_data = png_bytes('red')
        reply = _Reply(new_data)
        label = news_page.NewsImageLabel()
        url = 'https://example.invalid/a.png'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(news_page, 'scl_dir', return_value=root), \
                 patch.object(page, '_news_signature', 'sig'):
                page._image_generation = 3
                cache = page._image_cache_path(url, 'sig')
                cache.parent.mkdir(parents=True)
                cache.write_bytes(old_data)
                page._image_pending[reply] = (
                    label, [], url, 'sig', 3, True,
                )
                page._on_image_finished(reply)
                self.assertEqual(cache.read_bytes(), new_data)
        label.deleteLater()
        self._dispose(page)

    def test_manual_refresh_has_five_second_cooldown(self):
        page = NewsPage()
        page._cards = [{'kind': 'version'}]
        with patch.object(page, '_request_news') as request, \
             patch.object(news_page, 'show_toast') as toast:
            page.load(force=True)
            request.assert_called_once()
            self.assertFalse(page.refresh_btn.isEnabled())
            request.reset_mock()
            page.load(force=True)
            request.assert_not_called()
            toast.assert_called()
        page._refresh_cooldown_timer.stop()
        page._last_manual_refresh_at = time.monotonic() - 6.0
        with patch.object(page, '_request_news') as request:
            page.load(force=True)
            request.assert_called_once()
        self._dispose(page)

    def test_refresh_button_restores_after_cooldown(self):
        page = NewsPage()
        page._last_manual_refresh_at = time.monotonic()
        page._reply = None
        page._restore_refresh_button()
        self.assertFalse(page.refresh_btn.isEnabled())
        self.assertTrue(page._refresh_cooldown_timer.isActive())
        page._refresh_cooldown_timer.stop()
        page._finish_refresh_cooldown()
        self.assertTrue(page.refresh_btn.isEnabled())
        self._dispose(page)

    def test_force_request_bypasses_cached_image(self):
        page = NewsPage()
        url = 'https://example.invalid/a.png'
        label = news_page.NewsImageLabel()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(news_page, 'scl_dir', return_value=root), \
                 patch.object(page, '_news_signature', 'sig'):
                cache = page._image_cache_path(url, 'sig')
                cache.parent.mkdir(parents=True)
                cache.write_bytes(png_bytes())
                with patch.object(page, '_next_image') as nxt:
                    page.request_image(label, [url], force=True)
                kwargs = nxt.call_args.kwargs
                self.assertTrue(kwargs['force'])
                self.assertEqual(kwargs['signature'], 'sig')
        label.deleteLater()
        self._dispose(page)


if __name__ == '__main__':
    unittest.main()
