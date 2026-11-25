"""News archive parsing tests (no network / no popup)."""

from __future__ import annotations

import unittest

from ui.pages.news_archive import _xaml_url, parse_archive_xaml
from ui.pages.news_page import parse_news_page


ARCHIVE_XAML = '''<local:MyCard Title="版本列表">
  <local:MyListItem Title="奔赴荒野 | 26.3"
      Info="2026-11-20"
      EventType="打开版本"
      EventData="https://mcnews.meloong.com/Archive/26.3.json"
      Logo="pack://application:,,,/images/Blocks/Grass.png"
      ToolTip="查看 26.3" />
  <local:MyListItem Title="在 Wiki 查看全部"
      Info="外部页面"
      EventType="打开网页"
      EventData="https://example.com/wiki" />
</local:MyCard>
<local:MyCard Title="其他版本">
  <local:MyListItem Title="混沌立方 | 26.2"
      EventType="打开版本"
      EventData="https://mcnews.meloong.com/Archive/26.2.json" />
</local:MyCard>'''


class NewsArchiveParserTests(unittest.TestCase):
    def test_parse_groups_and_item_fields(self):
        groups = parse_archive_xaml(ARCHIVE_XAML)
        self.assertEqual([g['title'] for g in groups], ['版本列表', '其他版本'])
        first = groups[0]['items'][0]
        self.assertEqual(first['title'], '奔赴荒野 | 26.3')
        self.assertEqual(first['info'], '2026-11-20')
        self.assertEqual(first['event_type'], '打开版本')
        self.assertEqual(
            first['event_data'],
            'https://mcnews.meloong.com/Archive/26.3.json',
        )
        self.assertTrue(first['logo'].endswith('Grass.png'))
        self.assertEqual(first['tooltip'], '查看 26.3')

    def test_parse_without_card_wrapper(self):
        groups = parse_archive_xaml(
            '<local:MyListItem Title="26.3" EventData="a.json" />'
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['title'], '')
        self.assertEqual(groups[0]['items'][0]['title'], '26.3')

    def test_items_without_event_data_are_ignored(self):
        groups = parse_archive_xaml(
            '<local:MyCard Title="空">'
            '<local:MyListItem Title="仅标题" />'
            '</local:MyCard>'
        )
        self.assertEqual(groups, [])

    def test_json_url_converts_to_xaml(self):
        self.assertEqual(
            _xaml_url('https://example.com/Archive.json?ver=1'),
            'https://example.com/Archive.xaml?ver=1',
        )
        self.assertEqual(
            _xaml_url('https://example.com/Archive.xaml'),
            'https://example.com/Archive.xaml',
        )

    def test_news_history_uses_event_data_instead_of_wiki(self):
        news = '''<local:MyCard Title="历史版本更新日志">
          <local:MyListItem Title="历史版本更新日志"
              Info="点击查看更多更新日志"
              EventType="打开版本列表"
              EventData="https://mcnews.meloong.com/VersionArchiveList.json" />
        </local:MyCard>'''
        items = parse_news_page(news)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], 'history')
        self.assertEqual(items[0]['event_type'], '打开版本列表')
        self.assertEqual(
            items[0]['event_data'],
            'https://mcnews.meloong.com/VersionArchiveList.json',
        )
        self.assertNotIn('zh.minecraft.wiki', str(items[0]))


if __name__ == '__main__':
    unittest.main()
