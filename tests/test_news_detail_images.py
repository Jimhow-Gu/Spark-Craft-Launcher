"""Version detail image and article metadata parser tests."""

from __future__ import annotations

import unittest

from ui.pages.news_detail import (
    VERSION_IMAGE_OVERRIDES,
    detail_xaml_image_info,
    detail_xaml_image_urls,
    og_image_from_html,
)


DETAIL_XAML = '''<local:MyImage
    Source="https://image.bugjump.net/news/version/26.1/26.1.jpeg"
    FallbackSource="https://i0.hdslb.com/fake-placeholder.webp"
    Style="{StaticResource VersionImage}"/>
<local:MyTextButton Text="无关文章"
    EventData="https://www.minecraft.net/en-us/article/removing-obfuscation-in-java-edition"/>
<local:MyTextButton Text="版本文章"
    EventData="https://minecraft.net/article/minecraft-java-edition-26-1"/>'''


class NewsDetailImageTests(unittest.TestCase):
    def test_image_info_separates_source_fallback_and_article(self):
        info = detail_xaml_image_info(DETAIL_XAML, '26.1')
        self.assertTrue(info['source'].endswith('/26.1.jpeg'))
        self.assertTrue(info['fallback'].endswith('fake-placeholder.webp'))
        self.assertEqual(
            info['article'],
            'https://minecraft.net/article/minecraft-java-edition-26-1',
        )

    def test_legacy_urls_keep_source_before_fallback(self):
        urls = detail_xaml_image_urls(DETAIL_XAML)
        self.assertEqual(len(urls), 2)
        self.assertIn('26.1.jpeg', urls[0])
        self.assertIn('fake-placeholder', urls[1])

    def test_26_1_has_real_official_cover_override(self):
        url = VERSION_IMAGE_OVERRIDES['26.1']
        self.assertIn('www.minecraft.net/content/dam/minecraftnet', url)
        self.assertTrue(url.endswith('26.1_1170x500.jpg'))

    def test_og_image_property_order_is_independent(self):
        first = '<meta property="og:image" content="https://img.example/a.jpg">'
        second = '<meta content="https://img.example/b.jpg" name="twitter:image">'
        self.assertEqual(og_image_from_html(first), 'https://img.example/a.jpg')
        self.assertEqual(og_image_from_html(second), 'https://img.example/b.jpg')

    def test_og_image_source_link(self):
        html = '<link rel="image_src" href="https://img.example/link.jpg">'
        self.assertEqual(og_image_from_html(html), 'https://img.example/link.jpg')


if __name__ == '__main__':
    unittest.main()
