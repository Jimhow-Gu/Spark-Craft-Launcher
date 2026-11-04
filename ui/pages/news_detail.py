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
    QFrame, QHBoxLayout, QLabel, QScrollArea, QTextBrowser, QVBoxLayout, QWidget,
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


def detail_xaml_image_urls(source):
    '''提取版本详情顶部的版本图片地址（含备用地址）。'''

    match = re.search(r'<local:MyImage\b([^>]*)>', str(source or ''), re.S | re.I)
    if not match:
        return []
    urls = []
    for name in ('Source', 'FallbackSource'):
        url = _attr(match.group(1), name)
        if url.startswith(('http://', 'https://')) and url not in urls:
            urls.append(url)
    return urls


def detail_xaml_to_html(source, light=False):
    '''把 PCL 版本详情 XAML 的主要文字和列表转换为 HTML。'''

    source = str(source or '')
    start = source.find('<FlowDocument>')
    end = source.rfind('</FlowDocument>')
    if start < 0 or end <= start:
        return ''
    body = source[start + len('<FlowDocument>'):end]

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

    body = re.sub(r'<Paragraph\b([^>]*)>(.*?)</Paragraph>', paragraph, body, flags=re.S)
    body = re.sub(r'<ListItem\b[^>]*>', '<li>', body, flags=re.I)
    body = re.sub(r'</ListItem>', '</li>', body, flags=re.I)
    body = re.sub(r'<List\b[^>]*>', '<ul>', body, flags=re.I)
    body = re.sub(r'</List>', '</ul>', body, flags=re.I)
    body = re.sub(r'<(?!/?(?:a|b|i|u|br|p|ul|li|h[1-4])\b)[^>]*>', '', body)
    text_color = '#1b1b1b' if light else '#f0f1f3'
    accent = '#2f6f6f' if light else '#bfe3e3'
    return (
        '<html><head><style>'
        f'body {{ color:{text_color}; font-family:"Microsoft YaHei UI"; font-size:14px; line-height:1.65; }}'
        f'h1,h2,h3,h4 {{ color:{accent}; margin:14px 0 6px 0; }}'
        'p { margin:6px 0; } ul { margin:4px 0 8px 18px; } li { margin:3px 0; }'
        f'a {{ color:{accent}; }}'
        '</style></head><body>' + body + '</body></html>'
    )


class VersionDetailPage(QWidget):
    back_requested = Signal()

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self._data = dict(data or {})
        self._reply = None
        self._image_reply = None
        self._image_urls = []
        self._image_cache_key = ''
        self._rendered_signature = ''
        self._manager = QNetworkAccessManager(self)
        version = str(self._data.get('version') or '')
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 0, 40, 24)
        header = QHBoxLayout()
        header.setContentsMargins(0, 24, 0, 8)
        header.setSpacing(10)
        back = BackButton()
        back.setStyleSheet(
            f'QPushButton {{ background:transparent; color:{self._text_color()}; border:none;'
            ' border-radius:19px; }'
            'QPushButton:hover { background:rgba(136,221,221,0.18); }'
        )
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        heading = version or str(self._data.get('title') or '').strip()
        title = QLabel(f'{heading}版本详情页' if heading else '版本详情页')
        title.setFont(ui_font(24, 700))
        title.setStyleSheet(f'color:{self._text_color()}; font-size:24px; font-weight:900;')
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        if self._data.get('date'):
            date = QLabel(f"最后更新：{self._data['date']}")
            date.setContentsMargins(0, 0, 0, 8)
            date.setStyleSheet(f'color:{self._muted_color()}; font-size:12px;')
            root.addWidget(date)

        light = self._is_light()
        card_bg = 'rgba(255,255,255,0.92)' if light else 'rgba(30,41,41,0.88)'
        card_border = 'rgba(0,0,0,0.14)' if light else 'rgba(255,255,255,0.18)'
        self.card = QFrame()
        self.card.setObjectName('VersionDetailCard')
        self.card.setAttribute(Qt.WA_StyledBackground, True)
        self.card.setStyleSheet(
            f'QFrame#VersionDetailCard {{ background:{card_bg};'
            f' border:1px solid {card_border}; border-radius:12px; }}'
            'QFrame#VersionDetailCard QLabel { background:transparent; border:none; }'
        )
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 12, 18, 14)
        card_layout.setSpacing(8)

        status = QHBoxLayout()
        status.setContentsMargins(0, 0, 0, 0)
        self.loading_icon = FireworksLoader(36)
        self.loading_text = QLabel('正在加载版本详情...')
        self.loading_text.setStyleSheet(f'color:{self._muted_color()}; font-size:13px;')
        status.addWidget(self.loading_icon)
        status.addWidget(self.loading_text)
        status.addStretch(1)
        card_layout.addLayout(status)

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
        self.image_label = NewsImageLabel()
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
        root.addWidget(self.card, 1)

        version = str(self._data.get('version') or '')
        cached = self._cache_path(version)
        if cached.is_file():
            try:
                content = cached.read_text(encoding='utf-8', errors='replace')
            except OSError:
                content = ''
            if content:
                self._render(content)
                self.loading_icon.show()
                self.loading_text.setText('正在检查版本详情更新...')
        url = str(self._data.get('detail_url') or '')
        if not url and version:
            url = f'https://mcnews.meloong.com/VersionDetail.xaml?ver={version}'
        if not url:
            self.loading_icon.hide()
            self.loading_text.setText('没有可用的版本详情地址')
            return
        self._request(url, version)

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
            if not bool(ConfigManager().get('verify_ssl', False)):
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
        urls = detail_xaml_image_urls(content)
        key = hashlib.sha1('\n'.join(urls).encode('utf-8')).hexdigest() if urls else ''
        if key == self._image_cache_key:
            return
        self._image_cache_key = key
        self._image_urls = list(urls)
        if not urls:
            self.image_label.hide()
            return
        self.image_label.show()
        for url in urls:
            cached = self._cached_image(url)
            if cached is not None:
                self.image_label.set_source(cached)
                return
        self.image_label.set_loading_text('正在加载版本图片')
        self._request_image()

    def _cached_image(self, url):
        path = self._image_cache_path(url)
        if not path.is_file():
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return pixmap

    def _request_image(self):
        if not self._image_urls:
            self.image_label.setText('版本图片加载失败')
            return
        url = self._image_urls.pop(0)
        cached = self._cached_image(url)
        if cached is not None:
            self.image_label.set_source(cached)
            return
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'User-Agent', b'PMGL-Launcher/1.1')
        try:
            request.setTransferTimeout(12000)
        except Exception:
            pass
        reply = self._manager.get(request)
        try:
            if not bool(ConfigManager().get('verify_ssl', False)):
                reply.sslErrors.connect(lambda _errors, r=reply: r.ignoreSslErrors())
        except Exception:
            pass
        reply.finished.connect(lambda r=reply, u=url: self._on_image_finished(r, u))
        self._image_reply = reply

    def _on_image_finished(self, reply, url):
        if reply is not self._image_reply:
            reply.deleteLater()
            return
        self._image_reply = None
        data = b''
        if reply.error() == QNetworkReply.NoError:
            try:
                length = int(reply.header(QNetworkRequest.ContentLengthHeader) or 0)
            except Exception:
                length = 0
            if length <= MAX_IMAGE_BYTES:
                raw = bytes(reply.readAll())
                if len(raw) <= MAX_IMAGE_BYTES:
                    data = raw
        pixmap = pixmap_from_data(data)
        reply.deleteLater()
        if not pixmap.isNull():
            try:
                path = self._image_cache_path(url)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except OSError:
                pass
            self.image_label.set_source(pixmap)
            return
        self._request_image()

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

    def _is_light(self):
        try:
            return self.palette().color(QPalette.Window).lightness() > 128
        except Exception:
            return False

    def _text_color(self):
        return '#1b1b1b' if self._is_light() else '#f0f1f3'

    def _muted_color(self):
        return '#3f6666' if self._is_light() else '#bfe3e3'

    def _render(self, content):
        signature = hashlib.sha1(str(content).encode('utf-8')).hexdigest()
        if signature == self._rendered_signature:
            self.loading_icon.hide()
            self.loading_text.setText('')
            return
        self._rendered_signature = signature
        old_scroll = self.scroll.verticalScrollBar().value()
        try:
            html_text = detail_xaml_to_html(content, self._is_light())
            if not html_text:
                self.loading_icon.hide()
                self.loading_text.setText('无法解析版本详情内容')
                return
            self._load_images(content)
            self.browser.setHtml(html_text)
            self._fit_browser()
            QTimer.singleShot(0, self._fit_browser)
            self.scroll.verticalScrollBar().setValue(old_scroll)
        except Exception as exc:
            logger.warning('渲染版本详情失败', exc_info=True)
            reason = str(exc).strip() or type(exc).__name__
            self.loading_icon.hide()
            self.loading_text.setText(f'版本详情内容加载失败：{reason}')
            return
        self.loading_icon.hide()
        self.loading_text.setText('')

    def _on_loaded(self, text, version):
        try:
            cache = self._cache_path(version)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding='utf-8')
        except Exception:
            pass
        self._render(text)

    def _on_error(self, message):
        self.loading_icon.hide()
        if self.browser.toPlainText().strip():
            self.loading_text.setText('网络不可用，当前显示缓存内容')
        else:
            self.loading_text.setText(f'版本详情加载失败：{message}')

    def shutdown(self):
        self.loading_icon.stop()
        self.image_label.stop()
        for attr in ('_reply', '_image_reply'):
            reply = getattr(self, attr, None)
            setattr(self, attr, None)
            if reply is not None:
                try:
                    reply.abort()
                    reply.deleteLater()
                except RuntimeError:
                    pass
