'''新闻页的 Minecraft 版本详情页面。'''

from __future__ import annotations

import hashlib
import html
import logging
import re

from PySide6.QtCore import QEvent, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QPalette, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTextBrowser,
    QVBoxLayout, QWidget,
)

from core.app_font import ui_font
from core.config import ConfigManager
from core.paths import scl_dir
from ui.widgets.back_button import BackButton
from ui.widgets.fireworks_loader import FireworksLoader
from ui.widgets.news_visuals import NewsImageLabel, pixmap_from_data

NEWS_UA = 'PCL2/2.13'
MAX_DETAIL_XAML_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
logger = logging.getLogger('NewsDetail')


def _text(value):
    value = html.unescape(str(value or ''))
    return re.sub(r'\s+', ' ', value).strip()


def _attr(attrs, name):
    match = re.search(rf'\b{re.escape(name)}="([^"]*)"', attrs or '', re.I)
    return _text(match.group(1)) if match else ''


KNOWN_PLACEHOLDER_IMAGE_SHA1 = {
    # 26.1 的 FallbackSource 是一张写着“暂时无法获取版本图片”的假图。
    'e31174412002ffdbe9d23b4d62de192004be1899',
}

VERSION_IMAGE_OVERRIDES = {
    # 26.1 的 Source 已 404；这个才是“小鬼当家”版本的真实官方封面。
    '26.1': (
        'https://www.minecraft.net/content/dam/minecraftnet/games/'
        'minecraft/screenshots/26.1_1170x500.jpg'
    ),
}


def _minecraft_article_url(source, version=''):
    '''从详情 XAML 里找与当前版本最匹配的 minecraft.net 文章。'''

    candidates = []
    for raw in re.findall(r'https?://[^\s"<>]+', str(source or '')):
        url = html.unescape(raw).rstrip('.,;)')
        if re.match(
            r'https?://(?:www\.)?minecraft\.net/(?:[a-z]{2}-[a-z]{2}/)?article/',
            url,
            re.I,
        ):
            candidates.append(url)
    if not candidates:
        return ''
    digits = re.findall(r'\d+', str(version or ''))
    joined = '-'.join(digits)

    def score(url):
        slug = url.rsplit('/', 1)[-1].lower()
        value = 0
        if 'minecraft-java-edition' in slug:
            value += 4
        if joined and joined in slug:
            value += 20
        elif digits:
            value += sum(1 for part in digits if part in slug)
        return value

    return max(candidates, key=score)


def detail_xaml_image_info(source, version=''):
    '''返回主图、备用图和文章地址；备用图不能当作主图缓存。'''

    match = re.search(
        r'<local:MyImage\b([^>]*)>', str(source or ''), re.S | re.I
    )
    attrs = match.group(1) if match else ''
    return {
        'source': _attr(attrs, 'Source'),
        'fallback': _attr(attrs, 'FallbackSource'),
        'article': _minecraft_article_url(source, version),
    }


def detail_xaml_image_urls(source):
    '''兼容旧调用：只返回主图/备用图，不包含文章地址。'''

    info = detail_xaml_image_info(source)
    return [
        url for url in (info['source'], info['fallback'])
        if url.startswith(('http://', 'https://'))
    ]


def og_image_from_html(text):
    '''从文章 HTML 中读取 og:image/twitter:image，兼容属性顺序。'''

    for meta in re.findall(r'<meta\b[^>]*>', str(text or ''), re.S | re.I):
        if not re.search(
            r'(?:property|name)\s*=\s*["\'](?:og:image(?::url)?|twitter:image)["\']',
            meta,
            re.I,
        ):
            continue
        content = re.search(
            r'content\s*=\s*["\']([^"\']+)["\']', meta, re.I
        )
        if content:
            url = html.unescape(content.group(1)).strip()
            if url.startswith(('http://', 'https://')):
                return url
    link = re.search(
        r'<link\b[^>]*rel\s*=\s*["\']image_src["\'][^>]*'
        r'href\s*=\s*["\']([^"\']+)["\']',
        str(text or ''),
        re.I,
    )
    if link:
        url = html.unescape(link.group(1)).strip()
        if url.startswith(('http://', 'https://')):
            return url
    return ''


def detail_xaml_to_html(source, light=False):
    '''把 PCL 版本详情 XAML 的主要文字和列表转换为 HTML。'''

    source = str(source or '')
    start = source.find('<FlowDocument>')
    end = source.rfind('</FlowDocument>')
    if start < 0 or end <= start:
        return ''
    body = source[start + len('<FlowDocument>'):end]
    if light:
        devnote_bg = '#F4F9CD'
        devnote_border = '#7FAE36'
        devnote_title = '#5E8626'
        devnote_text = '#26351B'
        devnote_link = '#356B19'
    else:
        devnote_bg = '#293223'
        devnote_border = '#9CCB5B'
        devnote_title = '#C7E98F'
        devnote_text = '#E7EEDB'
        devnote_link = '#B9E986'

    def replace_button(match):
        attrs, inner = match.group(1), match.group(2)
        label = _attr(attrs, 'Text') or _text(re.sub(r'<[^>]+>', '', inner))
        url = _attr(attrs, 'EventData')
        if url.startswith(('http://', 'https://')):
            return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
        return html.escape(label)

    def inline(value):
        value = re.sub(
            r'<local:MyTextButton\b([^>]*)>(.*?)</local:MyTextButton>',
            replace_button, value, flags=re.S | re.I,
        )
        value = re.sub(r'<Bold>(.*?)</Bold>', r'<b>\1</b>', value, flags=re.S | re.I)
        value = re.sub(r'<Underline>(.*?)</Underline>', r'<u>\1</u>', value, flags=re.S | re.I)
        value = re.sub(r'<Italic>(.*?)</Italic>', r'<i>\1</i>', value, flags=re.S | re.I)
        value = re.sub(r'<LineBreak\s*/?>', '<br/>', value, flags=re.I)
        value = re.sub(r'<(?!/?(?:a|b|i|u|br)\b)[^>]*>', '', value)
        return value

    def paragraph(match):
        attrs, inner = match.group(1), match.group(2)
        style_match = re.search(r'Style="\{StaticResource (H[1-4]|Lp)\}"', attrs)
        style = style_match.group(1) if style_match else ''
        tag = {'H1': 'h1', 'H2': 'h2', 'H3': 'h3', 'H4': 'h4'}.get(style, 'p')
        return f'<{tag}>{inline(inner)}</{tag}>'

    def devnote_section(match):
        inner = match.group(1)
        # PCL 的开发者注释在 Section 内重复写了一次标题，这里去掉由 CSS 统一渲染。
        inner = re.sub(
            r'<Paragraph\b[^>]*>.*?开发者注释.*?</Paragraph>',
            '', inner, count=1, flags=re.S,
        )
        return (
            f'<table width="100%" cellspacing="0" cellpadding="9" bgcolor="{devnote_bg}" '
            f'style="background-color:{devnote_bg};">'
            f'<tr><td bgcolor="{devnote_bg}" style="border-left:3px solid {devnote_border};">'
            '<div class="devnote"><div class="devnote-title">开发者注释</div>'
            + inner + '</div></td></tr></table>'
        )

    body = re.sub(
        r'<Section\b[^>]*devnoteQuote[^>]*>(.*?)</Section>',
        devnote_section, body, flags=re.S | re.I,
    )
    body = re.sub(r'<Paragraph\b([^>]*)>(.*?)</Paragraph>', paragraph, body, flags=re.S)
    body = re.sub(r'<ListItem\b[^>]*>', '<li>', body, flags=re.I)
    body = re.sub(r'</ListItem>', '</li>', body, flags=re.I)
    body = re.sub(r'<List\b[^>]*>', '<ul>', body, flags=re.I)
    body = re.sub(r'</List>', '</ul>', body, flags=re.I)
    body = re.sub(r'<(?!/?(?:a|b|i|u|br|p|ul|li|h[1-4]|div|table|tr|td)\b)[^>]*>', '', body)
    text_color = '#1b1b1b' if light else '#f0f1f3'
    accent = '#2f6f6f' if light else '#bfe3e3'
    return (
        '<html><head><style>'
        f'body {{ margin:0; color:{text_color}; font-family:"Microsoft YaHei UI"; font-size:14px; line-height:1.65; }}'
        f'h1,h2,h3,h4 {{ color:{accent}; margin:14px 0 6px 0; }}'
        'p { margin:6px 0; } ul { margin:4px 0 8px 18px; } li { margin:3px 0; }'
        f'a {{ color:{accent}; }}'
        f'.devnote-title {{ color:{devnote_title}; font-weight:700; font-size:15px;'
        ' margin:0 0 5px 0; }'
        f'.devnote p, .devnote li {{ color:{devnote_text}; margin:5px 0; }}'
        f'.devnote a {{ color:{devnote_link}; }}'
        '</style></head><body>' + body + '</body></html>'
    )


class VersionDetailPage(QWidget):
    back_requested = Signal()

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self._data = dict(data or {})
        self._reply = None
        self._image_reply = None
        self._article_reply = None
        self._image_primary_url = ''
        self._image_fallback_url = ''
        self._image_article_url = ''
        self._image_stage = ''
        self._image_cache_key = ''
        self._rendered_signature = ''
        self._last_source = ''
        self._html_light = None
        self._has_content = False
        self._manager = QNetworkAccessManager(self)
        version = str(self._data.get('version') or '')
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 0, 40, 24)
        header = QHBoxLayout()
        header.setContentsMargins(0, 24, 0, 8)
        header.setSpacing(10)
        self.back_btn = BackButton()
        self.back_btn.clicked.connect(self.back_requested.emit)
        header.addWidget(self.back_btn)
        heading = version or str(self._data.get('title') or '').strip()
        self.title_label = QLabel(f'{heading}版本详情页' if heading else '版本详情页')
        self.title_label.setFont(ui_font(24, 700))
        header.addWidget(self.title_label)
        header.addStretch(1)
        root.addLayout(header)

        self.date_label = None
        if self._data.get('date'):
            self.date_label = QLabel(f"最后更新：{self._data['date']}")
            self.date_label.setContentsMargins(0, 0, 0, 8)
            root.addWidget(self.date_label)

        light = self._is_light()
        card_bg = 'rgba(255,255,255,0.92)' if light else 'rgba(30,41,41,0.88)'
        card_border = 'rgba(0,0,0,0.14)' if light else 'rgba(255,255,255,0.18)'
        self.card = QFrame()
        self.card.setObjectName('VersionDetailCard')
        self.card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 12, 18, 14)
        card_layout.setSpacing(8)

        retry_style = (
            'QPushButton { background:transparent; color:#88DDDD;'
            ' border:1px solid rgba(136,221,221,0.55); border-radius:6px;'
            ' padding:3px 12px; }'
            'QPushButton:hover { background:rgba(136,221,221,0.18); }'
        )

        # 有缓存正文时，更新状态作为右上角浮层，不参与布局，避免正文跳动。
        self.update_widget = QWidget(self.card)
        self.update_widget.setStyleSheet('background:transparent;')
        update_layout = QHBoxLayout(self.update_widget)
        update_layout.setContentsMargins(0, 0, 0, 0)
        update_layout.setSpacing(8)
        self.update_label = QLabel('')
        self.update_retry_btn = QPushButton('重试')
        self.update_retry_btn.setStyleSheet(retry_style)
        self.update_retry_btn.clicked.connect(self._retry)
        self.update_retry_btn.hide()
        update_layout.addWidget(self.update_label)
        update_layout.addWidget(self.update_retry_btn)
        self.update_widget.hide()

        # 初次加载：占满卡片并真正居中；缓存正文可用时整块隐藏。
        self.loading_panel = QWidget()
        self.loading_panel.setStyleSheet('background:transparent;')
        panel_layout = QVBoxLayout(self.loading_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)
        panel_layout.addStretch(1)
        self.loading_icon = FireworksLoader(48)
        self.loading_text = QLabel('正在加载版本详情...')
        loading_row = QHBoxLayout()
        loading_row.setSpacing(10)
        loading_row.addStretch(1)
        loading_row.addWidget(self.loading_icon)
        loading_row.addWidget(self.loading_text)
        loading_row.addStretch(1)
        panel_layout.addLayout(loading_row)
        self.retry_btn = QPushButton('重试')
        self.retry_btn.setStyleSheet(retry_style)
        self.retry_btn.clicked.connect(self._retry)
        self.retry_btn.hide()
        panel_layout.addWidget(self.retry_btn, 0, Qt.AlignHCenter)
        panel_layout.addStretch(1)
        card_layout.addWidget(self.loading_panel, 1)
        self._apply_header_theme(light)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            'QScrollArea { background: transparent; border: none; }'
            'QScrollArea > QWidget > QWidget { background: transparent; }'
        )
        self.content = QWidget()
        self.content.setStyleSheet('background: transparent;')
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 4, 0, 0)
        content_layout.setSpacing(14)
        self.image_label = NewsImageLabel(
            adaptive_height=True, max_height=420
        )
        self.image_label.hide()
        content_layout.addWidget(self.image_label)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setFrameShape(QFrame.NoFrame)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.browser.setStyleSheet('QTextBrowser { background: transparent; border: none; }')
        content_layout.addWidget(self.browser)
        self.scroll.setWidget(self.content)
        corner = QWidget(self.scroll)
        corner.setStyleSheet('background: transparent;')
        self.scroll.setCornerWidget(corner)
        self.scroll.viewport().installEventFilter(self)
        card_layout.addWidget(self.scroll, 1)
        self.scroll.hide()
        root.addWidget(self.card, 1)

        version = str(self._data.get('version') or '')
        cached = self._cache_path(version)
        has_content = False
        if cached.is_file():
            try:
                content = cached.read_text(encoding='utf-8', errors='replace')
            except OSError:
                content = ''
            if content:
                self._render(content)
                has_content = self._has_content
        if not has_content:
            inline_content = str(self._data.get('content_xaml') or '')
            if inline_content:
                # 新闻主页已经拿到完整正文时先立即显示，网络只用于检查更新。
                self._render(inline_content)
                has_content = self._has_content
        if has_content:
            self._show_update_status('正在检查版本详情更新...')
        url = str(self._data.get('detail_url') or '')
        self._detail_url = url
        if not url and version:
            url = f'https://mcnews.meloong.com/VersionDetail.xaml?ver={version}'
        if not url:
            if has_content:
                self._show_update_status('没有可用的版本详情更新地址')
            else:
                self.loading_icon.hide()
                self.loading_text.setText('没有可用的版本详情地址')
            return
        self._request(url, version)

    def _show_update_status(self, text, retry=False):
        self.update_label.setText(str(text))
        self.update_label.show()
        self.update_retry_btn.setVisible(bool(retry))
        self.update_widget.adjustSize()
        self.update_widget.show()
        self.update_widget.raise_()
        self._position_update_widget()

    def _hide_update_status(self):
        self.update_widget.hide()
        self.update_label.clear()
        self.update_retry_btn.hide()

    def _position_update_widget(self):
        try:
            if not self.update_widget.isVisible():
                return
            self.update_widget.adjustSize()
            x = max(8, self.card.width() - self.update_widget.width() - 16)
            self.update_widget.move(x, 8)
            self.update_widget.raise_()
        except RuntimeError:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._position_update_widget)

    def _retry(self):
        self.retry_btn.hide()
        self.update_retry_btn.hide()
        if self._has_content:
            self._show_update_status('正在重新检查版本详情更新...')
        else:
            self._hide_update_status()
            self.loading_panel.show()
            self.scroll.hide()
            self.loading_icon.show()
            self.loading_text.setText('正在重新加载版本详情...')
        self._request(self._detail_url, str(self._data.get('version') or ''))

    def _request(self, url, version):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'User-Agent', NEWS_UA.encode('ascii'))
        request.setRawHeader(b'Accept', b'application/xml')
        try:
            request.setTransferTimeout(15000)
        except Exception:
            pass
        reply = self._manager.get(request)
        try:
            if not bool(ConfigManager().get('verify_ssl', True)):
                reply.sslErrors.connect(lambda _errors, r=reply: r.ignoreSslErrors())
        except Exception:
            pass
        reply.finished.connect(lambda r=reply: self._on_reply_finished(r, version))
        self._reply = reply

    def _on_reply_finished(self, reply, version):
        if reply is not self._reply:
            reply.deleteLater()
            return
        self._reply = None
        error = ''
        if reply.error() != QNetworkReply.NoError:
            error = reply.errorString()
            text = ''
        else:
            try:
                length = int(reply.header(QNetworkRequest.ContentLengthHeader) or 0)
            except Exception:
                length = 0
            if length > MAX_DETAIL_XAML_BYTES:
                error = '版本详情内容过大'
            else:
                raw = bytes(reply.readAll())
                if len(raw) > MAX_DETAIL_XAML_BYTES:
                    error = '版本详情内容过大'
                else:
                    text = raw.decode('utf-8', errors='replace')
                    if '<FlowDocument>' not in text:
                        error = '版本详情内容异常'
        reply.deleteLater()
        if error:
            self._on_error(error)
        else:
            self._on_loaded(text, version)

    def _cache_path(self, version):
        safe = re.sub(r'[^0-9A-Za-z._-]', '_', str(version or 'version'))[:80]
        return scl_dir() / 'cache' / 'mcnews_details' / f'{safe}.xaml'

    def _image_cache_path(self, url):
        key = hashlib.sha1(str(url).encode('utf-8')).hexdigest()
        return scl_dir() / 'cache' / 'mcnews_detail_images' / key

    def _load_images(self, content):
        version = str(self._data.get('version') or '').strip()
        info = detail_xaml_image_info(content, version)
        primary = (
            VERSION_IMAGE_OVERRIDES.get(version)
            or info.get('source')
            or ''
        )
        fallback = str(info.get('fallback') or '')
        article = str(info.get('article') or '')
        key = primary or fallback or article
        if key == self._image_cache_key:
            return
        self._image_cache_key = key
        self._image_primary_url = primary
        self._image_fallback_url = fallback
        self._image_article_url = article
        if not (primary or fallback or article):
            self.image_label.hide()
            return
        self.image_label.show()
        if primary:
            cached = self._cached_image(primary)
            if cached is not None:
                self.image_label.set_source(cached)
                return
        self.image_label.set_loading_text('正在加载版本图片')
        if primary:
            self._request_image_url(primary, 'primary')
        else:
            self._request_article_image()

    def _cached_image(self, url):
        '''只读取主图缓存；备用图永远不会进入这里。'''

        path = self._image_cache_path(url)
        if not path.is_file():
            return None
        try:
            raw = path.read_bytes()
        except OSError:
            raw = b''
        if raw and hashlib.sha1(raw).hexdigest() in KNOWN_PLACEHOLDER_IMAGE_SHA1:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return pixmap

    def _request_image_url(self, url, stage):
        if not url:
            self._next_image_stage(stage)
            return
        if self._image_reply is not None:
            try:
                self._image_reply.abort()
                self._image_reply.deleteLater()
            except RuntimeError:
                pass
        self._image_stage = stage
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'User-Agent', b'PMGL-Launcher/1.1')
        try:
            request.setTransferTimeout(12000)
        except Exception:
            pass
        reply = self._manager.get(request)
        try:
            if not bool(ConfigManager().get('verify_ssl', True)):
                reply.sslErrors.connect(
                    lambda _errors, r=reply: r.ignoreSslErrors()
                )
        except Exception:
            pass
        reply.finished.connect(
            lambda r=reply, u=url, s=stage: self._on_image_finished(r, u, s)
        )
        self._image_reply = reply

    def _request_article_image(self):
        '''主图失败后从 minecraft.net 文章读 og:image。'''

        url = self._image_article_url
        if not url or url == self._image_primary_url:
            self._request_fallback_image()
            return
        if self._article_reply is not None:
            try:
                self._article_reply.abort()
                self._article_reply.deleteLater()
            except RuntimeError:
                pass
        self.image_label.set_loading_text('正在获取版本封面')
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'User-Agent', b'Mozilla/5.0')
        request.setRawHeader(
            b'Accept', b'text/html,application/xhtml+xml'
        )
        try:
            request.setTransferTimeout(12000)
        except Exception:
            pass
        reply = self._manager.get(request)
        try:
            if not bool(ConfigManager().get('verify_ssl', True)):
                reply.sslErrors.connect(
                    lambda _errors, r=reply: r.ignoreSslErrors()
                )
        except Exception:
            pass
        reply.finished.connect(
            lambda r=reply: self._on_article_finished(r)
        )
        self._article_reply = reply

    def _on_article_finished(self, reply):
        if reply is not self._article_reply:
            reply.deleteLater()
            return
        self._article_reply = None
        html_text = ''
        if reply.error() == QNetworkReply.NoError:
            try:
                raw = bytes(reply.readAll())
            except Exception:
                raw = b''
            if len(raw) <= 4 * 1024 * 1024:
                html_text = raw.decode('utf-8', errors='replace')
        reply.deleteLater()
        image_url = og_image_from_html(html_text)
        if image_url and image_url not in (
            self._image_primary_url, self._image_fallback_url
        ):
            self._request_image_url(image_url, 'article')
            return
        self._request_fallback_image()

    def _request_fallback_image(self):
        url = self._image_fallback_url
        if (
            not url
            or url == self._image_primary_url
            or self._image_stage == 'fallback'
        ):
            self._show_image_unavailable()
            return
        self.image_label.set_loading_text('正在加载备用图片')
        self._request_image_url(url, 'fallback')

    def _show_image_unavailable(self):
        self.image_label.setText('版本图片暂不可用')
        self._image_stage = ''

    def _next_image_stage(self, stage):
        if stage == 'primary':
            self._request_article_image()
        elif stage == 'article':
            self._request_fallback_image()
        else:
            self._show_image_unavailable()

    def _on_image_finished(self, reply, url, stage):
        if reply is not self._image_reply:
            reply.deleteLater()
            return
        self._image_reply = None
        data = b''
        if reply.error() == QNetworkReply.NoError:
            try:
                length = int(
                    reply.header(QNetworkRequest.ContentLengthHeader) or 0
                )
            except Exception:
                length = 0
            if length <= MAX_IMAGE_BYTES:
                raw = bytes(reply.readAll())
                if len(raw) <= MAX_IMAGE_BYTES:
                    data = raw
        pixmap = pixmap_from_data(data)
        reply.deleteLater()
        known_placeholder = (
            hashlib.sha1(data).hexdigest() in KNOWN_PLACEHOLDER_IMAGE_SHA1
            if data else False
        )
        if not pixmap.isNull() and not known_placeholder:
            if stage == 'primary':
                try:
                    path = self._image_cache_path(url)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                except OSError:
                    pass
            self.image_label.set_source(pixmap)
            self._image_stage = ''
            return
        # 备用图只用于这一次显示，不写缓存；带文字的假图直接跳过。
        self._next_image_stage(stage)

    def _fit_browser(self):
        try:
            width = max(120, self.scroll.viewport().width() - 2)
            document = self.browser.document()
            document.setTextWidth(width)
            height = max(1, int(document.size().height()) + 6)
            if abs(height - self.browser.height()) > 1:
                self.browser.setFixedHeight(height)
        except RuntimeError:
            pass

    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport() and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._fit_browser)
        return super().eventFilter(obj, event)

    def _apply_header_theme(self, light):
        text_color = '#1b1b1b' if light else '#f0f1f3'
        muted = '#3f6666' if light else '#bfe3e3'
        card_bg = 'rgba(255,255,255,0.92)' if light else 'rgba(30,41,41,0.88)'
        card_border = 'rgba(0,0,0,0.14)' if light else 'rgba(255,255,255,0.18)'
        widgets = (
            self.back_btn, self.title_label, self.date_label, self.card,
            self.loading_text, self.update_label,
        )
        for widget in widgets:
            if widget is not None:
                widget.setProperty('_theme_ignore', True)
        self.back_btn.setStyleSheet(
            f'QPushButton {{ background:transparent; color:{text_color}; border:none;'
            ' border-radius:19px; }'
            'QPushButton:hover { background:rgba(136,221,221,0.18); }'
        )
        self.title_label.setStyleSheet(
            f'color:{text_color}; font-size:24px; font-weight:900;'
        )
        if self.date_label is not None:
            self.date_label.setStyleSheet(f'color:{muted}; font-size:12px;')
        self.loading_text.setStyleSheet(f'color:{muted}; font-size:13px;')
        self.update_label.setStyleSheet(
            f'color:{muted}; font-size:12px; font-weight:500;'
        )
        self.card.setStyleSheet(
            f'QFrame#VersionDetailCard {{ background:{card_bg};'
            f' border:1px solid {card_border}; border-radius:12px; }}'
            'QFrame#VersionDetailCard QLabel { background:transparent; border:none; }'
        )

    def set_light(self, light):
        light = bool(light)
        self._apply_header_theme(light)
        if not self._last_source or self._html_light == light:
            return
        try:
            html_text = detail_xaml_to_html(self._last_source, light)
            if not html_text:
                return
            old_scroll = self.scroll.verticalScrollBar().value()
            self.browser.setHtml(html_text)
            self._fit_browser()
            QTimer.singleShot(0, self._fit_browser)
            self.scroll.verticalScrollBar().setValue(old_scroll)
            self._html_light = light
        except RuntimeError:
            pass
        except Exception:
            logger.warning('切换版本详情主题失败', exc_info=True)

    def _is_light(self):
        try:
            return self.palette().color(QPalette.Window).lightness() > 128
        except Exception:
            return False

    def _text_color(self):
        return '#1b1b1b' if self._is_light() else '#f0f1f3'

    def _muted_color(self):
        return '#3f6666' if self._is_light() else '#bfe3e3'

    def _finish_content(self):
        self._has_content = True
        self.loading_panel.hide()
        self.loading_icon.stop()
        self.scroll.show()
        self._hide_update_status()
        self.retry_btn.hide()

    def _show_loading_failure(self, text):
        if self._has_content:
            self._show_update_status(text, retry=True)
            return
        self.loading_panel.show()
        self.scroll.hide()
        self.loading_icon.hide()
        self.loading_text.setText(str(text))
        self.retry_btn.show()

    def _render(self, content):
        signature = hashlib.sha1(str(content).encode('utf-8')).hexdigest()
        if signature == self._rendered_signature:
            self._finish_content()
            return
        self._rendered_signature = signature
        old_scroll = self.scroll.verticalScrollBar().value()
        try:
            light = self._is_light()
            html_text = detail_xaml_to_html(content, light)
            if not html_text:
                self._show_loading_failure('无法解析版本详情内容')
                return
            self._load_images(content)
            self.browser.setHtml(html_text)
            self._fit_browser()
            QTimer.singleShot(0, self._fit_browser)
            self.scroll.verticalScrollBar().setValue(old_scroll)
            self._last_source = str(content)
            self._html_light = light
        except Exception:
            logger.warning('渲染版本详情失败', exc_info=True)
            self._show_loading_failure('版本详情内容加载失败，请点击重试')
            return
        self._finish_content()

    def _on_loaded(self, text, version):
        try:
            cache = self._cache_path(version)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding='utf-8')
        except Exception:
            pass
        self._render(text)

    def _on_error(self, message):
        logger.warning('版本详情加载失败: %s', message)
        self._show_loading_failure(
            '网络不可用，当前显示缓存内容'
            if self._has_content else '版本详情加载失败，请点击重试'
        )

    def shutdown(self):
        self.loading_icon.stop()
        self.image_label.stop()
        for attr in ('_reply', '_image_reply', '_article_reply'):
            reply = getattr(self, attr, None)
            setattr(self, attr, None)
            if reply is not None:
                try:
                    reply.abort()
                    reply.deleteLater()
                except RuntimeError:
                    pass
