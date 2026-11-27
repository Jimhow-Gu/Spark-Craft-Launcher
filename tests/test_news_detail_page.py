"""Version detail image flow tests (offscreen, no network)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from ui.pages.news_detail import VersionDetailPage, VERSION_IMAGE_OVERRIDES


XAML = '''<local:MyImage
    Source="https://image.bugjump.net/news/version/26.1/26.1.jpeg"
    FallbackSource="https://i0.hdslb.com/fake-placeholder.webp"/>
<local:MyTextButton EventData="https://minecraft.net/article/minecraft-java-edition-26-1"/>'''


class NewsDetailPageImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_26_1_uses_real_cover_and_checks_only_primary_cache(self):
        with patch.object(VersionDetailPage, '_request', lambda *args: None):
            page = VersionDetailPage(
                {'version': '26.1', 'detail_url': 'offline'}
            )
        cache_calls = []
        request_calls = []
        page._image_cache_key = ''
        page._cached_image = lambda url: (
            cache_calls.append(url) or None
        )
        page._request_image_url = lambda url, stage: (
            request_calls.append((url, stage))
        )
        page._load_images(XAML)
        self.assertEqual(
            request_calls,
            [(VERSION_IMAGE_OVERRIDES['26.1'], 'primary')],
        )
        self.assertEqual(cache_calls, [VERSION_IMAGE_OVERRIDES['26.1']])
        page.deleteLater()

    def test_fallback_failure_uses_plain_placeholder(self):
        with patch.object(VersionDetailPage, '_request', lambda *args: None):
            page = VersionDetailPage(
                {'version': '26.1', 'detail_url': 'offline'}
            )
        texts = []
        page.image_label.setText = texts.append
        page._next_image_stage('fallback')
        self.assertEqual(texts, ['版本图片暂不可用'])
        page.deleteLater()

    def test_cached_content_status_is_right_overlay_without_layout_jump(self):
        with patch.object(VersionDetailPage, '_request', lambda *args: None):
            page = VersionDetailPage({
                'version': 'test',
                'detail_url': 'offline',
                'content_xaml': (
                    '<FlowDocument><Paragraph><Run>正文</Run></Paragraph>'
                    '</FlowDocument>'
                ),
            })
        page.resize(900, 600)
        page.show()
        self.app.processEvents()
        self.assertTrue(page.update_widget.isVisible())
        self.assertGreater(page.update_widget.x(), page.card.width() // 2)
        scroll_y = page.scroll.geometry().y()
        page._finish_content()
        self.app.processEvents()
        self.assertFalse(page.update_widget.isVisible())
        self.assertEqual(page.scroll.geometry().y(), scroll_y)
        page.close()
        page.deleteLater()

    def test_primary_failure_goes_to_article_before_fallback(self):
        with patch.object(VersionDetailPage, '_request', lambda *args: None):
            page = VersionDetailPage(
                {'version': '26.1', 'detail_url': 'offline'}
            )
        article_calls = []
        page._request_article_image = lambda: article_calls.append(True)
        page._next_image_stage('primary')
        self.assertEqual(article_calls, [True])
        page.deleteLater()


if __name__ == '__main__':
    unittest.main()
