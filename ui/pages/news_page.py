'''主页 - Minecraft 新闻页。

数据来源与 PCL2 新闻主页一致：https://mcnews.meloong.com
默认展开最新版本，历史版本/反馈折叠显示。
'''

from __future__ import annotations

import hashlib
import html
import logging
import re
from pathlib import Path

from PySide6.QtCore import (Qt, QEvent, QEasingCurve, QPropertyAnimation, QSize, QTimer, QUrl)
from PySide6.QtGui import QDesktopServices, QIcon, QPalette, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from core.app_font import ui_font
from core.config import ConfigManager
from core.paths import safe_rmtree, scl_dir
from ui.pages.news_detail import VersionDetailPage
from ui.widgets.news_visuals import NewsImageLabel, TranslationPending, pixmap_from_data
from ui.widgets.toast import show_toast

NEWS_URL = 'https://mcnews.meloong.com'
MAX_NEWS_XAML_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
logger = logging.getLogger('NewsPage')
NEWS_SOURCE_URL = 'https://github.com/Light-Beacon/PCL2-NewsHomepage'
NEWS_UA = 'PCL2/2.13'


def _text(value):
    value = html.unescape(str(value or ''))
    return re.sub(r'\s+', ' ', value).strip()


def _attr(attrs, name):
    match = re.search(rf'\b{re.escape(name)}="([^"]*)"', attrs or '', re.I)
    return _text(match.group(1)) if match else ''


def parse_news_page(text):
    '''从 PCL 新闻主页 XAML 中提取版本、历史日志和反馈卡片。'''

    items = []
    for match in re.finditer(
        r'<local:MyCard\b([^>]*)>(.*?)</local:MyCard>',
        str(text or ''), re.S,
    ):
        attrs, body = match.group(1), match.group(2)
        title = _attr(attrs, 'Title')
        version_match = re.search(
            r'<TextBlock[^>]*VersionTitleBlock[^>]*\bText="([^"]*)"',
            body, re.S,
        )
        if version_match:
            version = _text(version_match.group(1))
            if not version:
                continue
            date_match = re.search(r'Text="最后更新：([^"]*)"', body)
            image_urls = []
            for image_attrs in re.findall(r'<local:MyImage\b([^>]*?)/>', body, re.S):
                for key in ('Source', 'FallbackSource'):
                    url = _attr(image_attrs, key)
                    if url and url not in image_urls:
                        image_urls.append(url)
            links = []
            for button_attrs in re.findall(r'<local:MyIconTextButton\b(.*?)/>', body, re.S):
                label = _attr(button_attrs, 'Text')
                url = _attr(button_attrs, 'EventData')
                event = _attr(button_attrs, 'EventType')
                if not label or event != '打开网页' or not url.startswith(('http://', 'https://')):
                    continue
                if label == '启动' or all(label != item[0] for item in links):
                    if label != '启动':
                        links.append((label, url))
            detail_url = ''
            for item_attrs in re.findall(r'<local:MyListItem([^>]*)>', body, re.S):
                item_title = _attr(item_attrs, 'Title')
                if '更新日志' not in item_title:
                    continue
                detail_url = _attr(item_attrs, 'EventData')
                if detail_url:
                    detail_url = detail_url.replace('VersionDetail.json', 'VersionDetail.xaml')
                    break
            items.append({
                'kind': 'version',
                'title': title or version,
                'version': version,
                'date': _text(date_match.group(1)) if date_match else '',
                'images': image_urls,
                'links': links,
                'translation_pending': '翻译施工中' in body,
                'detail_url': detail_url,
            })
            continue
        if '历史版本更新日志' in body or '点击查看更多更新日志' in body:
            info_match = re.search(r'<local:MyListItem\b([^>]*)>', body, re.S)
            info_attrs = info_match.group(1) if info_match else ''
            items.append({
                'kind': 'history',
                'title': '历史版本更新日志',
                'info': _attr(info_attrs, 'Info') or '点击查看更多更新日志',
                'url': 'https://zh.minecraft.wiki/w/Java版版本记录',
            })
            continue
    return items


class NewsCard(QFrame):
    def __init__(self, data, page, *, expanded=True, parent=None):
        super().__init__(parent)
        self.setObjectName('NewsCard')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            'QFrame#NewsCard { background: rgba(30,41,41,0.88);'
            ' border: 1px solid rgba(255,255,255,0.18); border-radius: 12px; }'
            'QFrame#NewsCard QLabel { background: transparent; border: none; }'
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 14)
        layout.setSpacing(8)

        self.header_widget = QWidget()
        self.header_widget.setCursor(Qt.PointingHandCursor)
        self.header_widget.setAttribute(Qt.WA_StyledBackground, False)
        self.header_widget.installEventFilter(self)
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel(str(data.get('title') or data.get('version') or 'Minecraft 新闻'))
        title.setStyleSheet('color:#f4fbfb; font-size:17px; font-weight:900;')
        title.setWordWrap(True)
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        header.addWidget(title, 1)
        self.toggle_btn = QToolButton()
        self.toggle_btn.setFixedSize(30, 30)
        self.toggle_btn.setText('▲' if expanded else '▼')
        self.toggle_btn.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.toggle_btn.setStyleSheet(
            'QToolButton { border:none; background:transparent; color:#A8E6E6;'
            ' font-size:14px; }'
        )
        header.addWidget(self.toggle_btn)
        layout.addWidget(self.header_widget)

        self.body = QWidget()
        self._expanded = bool(expanded)
        self._body_anim = None
        self._page = page
        self._image_urls = list(data.get('images') or [])
        self._image_requested = False
        self.image_label = None
        self.body.setMaximumHeight(16777215 if expanded else 0)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)

        meta_text = f"版本 {data.get('version', '')}"
        meta = QLabel(meta_text)
        meta.setStyleSheet('color:#bfe3e3; font-size:12px;')
        body_layout.addWidget(meta)

        if self._image_urls:
            self.image_label = NewsImageLabel()
            body_layout.addWidget(self.image_label)
            if expanded:
                self._load_image()

        if data.get('translation_pending'):
            body_layout.addWidget(TranslationPending())

        if data.get('detail_url'):
            detail_btn = QPushButton('查看版本详情')
            detail_btn.setCursor(Qt.PointingHandCursor)
            detail_btn.setIcon(QIcon(str(Path(__file__).resolve().parent.parent / 'imgs' / 'Release.png')))
            detail_btn.setIconSize(QSize(20, 20))
            detail_btn.setToolTip('在主页内查看完整版本更新日志')
            detail_btn.setMinimumHeight(36)
            detail_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            detail_btn.setStyleSheet(
                'QPushButton { background:rgba(136,221,221,0.10); color:#d9f7f7;'
                ' border:1px solid rgba(136,221,221,0.38); border-radius:6px;'
                ' padding:6px 14px; text-align:left; font-weight:600; }'
                'QPushButton:hover { background:rgba(136,221,221,0.22); }'
                'QPushButton:pressed { background:rgba(136,221,221,0.30); }'
            )
            detail_btn.clicked.connect(lambda: page.open_version_detail(data))
            body_layout.addWidget(detail_btn)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        links = data.get('links') or []
        for label, url in links:
            button = QPushButton(label)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(30)
            button.setStyleSheet(
                'QPushButton { background:transparent; color:#A8E6E6;'
                ' border:1px solid rgba(136,221,221,0.62); border-radius:6px;'
                ' padding:5px 12px; font-weight:600; }'
                'QPushButton:hover { background:rgba(136,221,221,0.24); }'
            )
            button.clicked.connect(
                lambda checked=False, link=url: QDesktopServices.openUrl(QUrl(link))
            )
            bottom_row.addWidget(button)
        bottom_row.addStretch(1)
        if data.get('date'):
            date_label = QLabel(f"最后更新：{data['date']}")
            date_label.setStyleSheet('color:#bfe3e3; font-size:12px;')
            bottom_row.addWidget(date_label, 0, Qt.AlignRight)
        body_layout.addLayout(bottom_row)
        layout.addWidget(self.body)
        if not expanded:
            self.body.hide()
        self.toggle_btn.clicked.connect(self._toggle)

    def eventFilter(self, obj, event):
        if obj is self.header_widget and event.type() == QEvent.MouseButtonRelease:
            if event.button() == Qt.LeftButton:
                self._toggle()
                return True
        return super().eventFilter(obj, event)

    def _toggle(self):
        self._animate_body(not self._expanded)

    def _load_image(self):
        '''折叠卡片的图片延迟到展开时再下载，减少启动资源占用。'''

        if self._image_requested or self.image_label is None:
            return
        self._image_requested = True
        self._page.request_image(self.image_label, self._image_urls)

    def _animate_body(self, expand):
        self._expanded = bool(expand)
        self.toggle_btn.setText('▲' if expand else '▼')
        if expand:
            self._load_image()
        if self._body_anim is not None:
            try:
                self._body_anim.stop()
            except RuntimeError:
                pass
        if expand:
            self.body.show()
            self.body.setMaximumHeight(0)
            target = max(1, self.body.sizeHint().height())
            start = 0
        else:
            target = 0
            start = max(1, self.body.height())
            self.body.setMaximumHeight(start)
        animation = QPropertyAnimation(self.body, b'maximumHeight', self)
        animation.setDuration(190)
        animation.setStartValue(start)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        if expand:
            animation.finished.connect(
                lambda: self.body.setMaximumHeight(16777215)
            )
        else:
            animation.finished.connect(self.body.hide)
        self._body_anim = animation
        animation.start()


class HistoryCard(QFrame):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            'QFrame { background: rgba(30,41,41,0.88);'
            ' border: 1px solid rgba(255,255,255,0.18); border-radius: 10px; }'
            'QFrame QLabel { background: transparent; border: none; }'
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        icon = QLabel('▤')
        icon.setStyleSheet('color:#A8E6E6; font-size:20px;')
        layout.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(str(data.get('title') or '历史版本更新日志'))
        title.setStyleSheet('color:#f4fbfb; font-size:14px; font-weight:700;')
        info = QLabel(str(data.get('info') or '点击查看更多更新日志'))
        info.setStyleSheet('color:#bfe3e3; font-size:12px;')
        text.addWidget(title)
        text.addWidget(info)
        layout.addLayout(text, 1)
        button = QPushButton('查看')
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(
            'QPushButton { background:transparent; color:#A8E6E6;'
            ' border:1px solid rgba(136,221,221,0.62); border-radius:6px;'
            ' padding:5px 14px; } QPushButton:hover { background:rgba(136,221,221,0.24); }'
        )
        button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(str(data.get('url') or NEWS_SOURCE_URL)))
        )
        layout.addWidget(button)


class NewsSection(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 2)
        layout.setSpacing(10)
        for _ in range(2):
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setStyleSheet('color: rgba(136,221,221,0.45); background:rgba(136,221,221,0.45);')
            line.setFixedHeight(1)
            layout.addWidget(line, 1)
        label = QLabel(str(title))
        label.setStyleSheet('color:#A8E6E6; font-size:13px; font-weight:700;')
        layout.addWidget(label)
        for _ in range(2):
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setStyleSheet('color: rgba(136,221,221,0.45); background:rgba(136,221,221,0.45);')
            line.setFixedHeight(1)
            layout.addWidget(line, 1)


class NewsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('NewsPage')
        self._loaded = False
        self._reply = None
        self._image_manager = QNetworkAccessManager(self)
        self._image_pending = {}
        self._cards = []
        self._news_signature = ''
        self._error_notice_timer = QTimer(self)
        self._error_notice_timer.setSingleShot(True)
        self._error_notice_timer.timeout.connect(self._show_refresh_error_toast)
        self._last_error_reason = ''
        self._cache_prune_scheduled = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.news_view = QWidget()
        news_layout = QVBoxLayout(self.news_view)
        news_layout.setContentsMargins(0, 0, 0, 0)
        news_layout.setSpacing(0)
        self.stack.addWidget(self.news_view)
        self._detail_pages = {}
        self.header_widget = QWidget()
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(40, 24, 40, 8)
        title = QLabel('欢迎使用新闻主页！')
        title.setFont(ui_font(24, 700))
        title.setStyleSheet(f"color: {self._title_color()}; font-size:24px; font-weight:900;")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_btn = QPushButton('刷新')
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setFixedSize(92, 34)
        self.refresh_btn.setStyleSheet(
            'QPushButton { background:transparent; color:#A8E6E6;'
            ' border:1px solid rgba(136,221,221,0.62); border-radius:7px; }'
            'QPushButton:hover { background:rgba(136,221,221,0.24); }'
            'QPushButton:disabled { color:rgba(255,255,255,0.3); }'
        )
        self.refresh_btn.clicked.connect(lambda: self.load(force=True))
        header.addWidget(self.refresh_btn)
        news_layout.addWidget(self.header_widget)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        self.content = QWidget()
        self.content.setStyleSheet('background: transparent;')
        self.cards_layout = QVBoxLayout(self.content)
        self.cards_layout.setContentsMargins(40, 4, 40, 24)
        self.cards_layout.setSpacing(12)
        self.scroll.setWidget(self.content)
        news_layout.addWidget(self.scroll, 1)

        self.footer = QLabel(
            f'<a href="{NEWS_SOURCE_URL}" style="color:#88DDDD;">'
            '新闻数据来源：PCL2 新闻主页 · 最亮的信标</a>'
        )
        self.footer.setTextFormat(Qt.RichText)
        self.footer.setOpenExternalLinks(True)
        self.footer.setAlignment(Qt.AlignCenter)
        self.footer.setStyleSheet(f'color: {self._muted_color()}; font-size:11px; padding:6px;')
        news_layout.addWidget(self.footer)

        self._image_manager.finished.connect(self._on_image_finished)
        self._homepage_mode = 'preset'
        self.apply_homepage_mode()

    def _title_color(self):
        try:
            light = self.palette().color(QPalette.Window).lightness() > 128
        except Exception:
            light = False
        return '#1b1b1b' if light else '#f0f1f3'

    def _muted_color(self):
        try:
            light = self.palette().color(QPalette.Window).lightness() > 128
        except Exception:
            light = False
        return '#3f6666' if light else 'rgba(205,238,238,0.78)'

    def apply_homepage_mode(self, mode=None):
        if mode is None:
            mode = str(ConfigManager().get('homepage_mode', 'preset') or 'preset')
        self._homepage_mode = 'blank' if str(mode) == 'blank' else 'preset'
        visible = self._homepage_mode != 'blank'
        self.stack.setVisible(visible)
        if visible and self.isVisible() and not self._loaded:
            self.load()
        elif not visible:
            self._release_detail_pages()

    def showEvent(self, event):
        super().showEvent(event)
        if self._homepage_mode == 'blank':
            return
        if not self._loaded:
            self.load()

    def hideEvent(self, event):
        super().hideEvent(event)
        # 离开主页后详情页不会被再次显示（返回主页会先回到新闻列表），
        # 及时释放大文档和图片，避免长期占用内存。
        self._release_detail_pages()

    def shutdown(self):
        self._error_notice_timer.stop()
        for detail in list(self._detail_pages.values()):
            try:
                detail.shutdown()
            except RuntimeError:
                pass
        reply = self._reply
        self._reply = None
        if reply is not None:
            try:
                reply.abort()
                reply.deleteLater()
            except RuntimeError:
                pass

    def _cache_path(self):
        return scl_dir() / 'cache' / 'mcnews_home.xaml'

    def load(self, force=False):
        if self._reply is not None:
            return
        if not force and self._cards:
            return
        if not self._cards:
            try:
                cached = self._cache_path().read_text(encoding='utf-8', errors='replace')
            except OSError:
                cached = ''
            if cached:
                try:
                    items = parse_news_page(cached)
                    if items:
                        self._news_signature = hashlib.sha1(cached.encode('utf-8')).hexdigest()[:16]
                        self._error_notice_timer.stop()
                        self._render(items)
                except Exception:
                    logger.warning('解析缓存的新闻内容失败', exc_info=True)
        self._loaded = True
        self.refresh_btn.setEnabled(False)
        self._request_news()
        if not self._cache_prune_scheduled:
            self._cache_prune_scheduled = True
            QTimer.singleShot(2500, self._prune_caches)

    def _request_news(self):
        request = QNetworkRequest(QUrl(NEWS_URL))
        request.setRawHeader(b'User-Agent', NEWS_UA.encode('ascii'))
        request.setRawHeader(b'Accept', b'application/xml')
        try:
            request.setTransferTimeout(15000)
        except Exception:
            pass
        reply = self._image_manager.get(request)
        try:
            if not bool(ConfigManager().get('verify_ssl', False)):
                reply.sslErrors.connect(lambda _errors, r=reply: r.ignoreSslErrors())
        except Exception:
            pass
        reply.finished.connect(lambda r=reply: self._on_news_reply(r))
        self._reply = reply

    def _on_news_reply(self, reply):
        if reply is not self._reply:
            reply.deleteLater()
            return
        self._reply = None
        self.refresh_btn.setEnabled(True)
        error = ''
        text = ''
        if reply.error() != QNetworkReply.NoError:
            error = reply.errorString() or '网络请求失败'
        else:
            try:
                length = int(reply.header(QNetworkRequest.ContentLengthHeader) or 0)
            except Exception:
                length = 0
            if length > MAX_NEWS_XAML_BYTES:
                error = '新闻源返回内容过大'
            else:
                raw = bytes(reply.readAll())
                if len(raw) > MAX_NEWS_XAML_BYTES:
                    error = '新闻源返回内容过大'
                else:
                    text = raw.decode('utf-8', errors='replace')
                    if '<local:MyCard' not in text:
                        error = '新闻源返回内容异常'
        reply.deleteLater()
        if error:
            self._on_news_error(error)
        else:
            self._on_news_loaded(text)

    def _on_news_loaded(self, text):
        try:
            signature = hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]
            if self._cards and signature == self._news_signature:
                self._error_notice_timer.stop()
                return
            items = parse_news_page(text)
            if not items:
                self._on_news_error('新闻源没有可用的版本新闻')
                return
            self._news_signature = signature
            try:
                cache = self._cache_path()
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(text, encoding='utf-8')
            except OSError:
                pass
            self._render(items)
        except Exception as exc:
            logger.warning('渲染新闻内容失败', exc_info=True)
            reason = str(exc).strip() or type(exc).__name__
            self._on_news_error(f'新闻内容解析失败：{reason}')

    def _on_news_error(self, message):
        # 保留已缓存的新闻卡片，只延迟提示刷新失败。
        self._last_error_reason = str(message or '未知错误')
        if not self._cards:
            self._show_error(message)
        if self.isVisible():
            self._error_notice_timer.start(2500)

    def _show_refresh_error_toast(self):
        reason = str(getattr(self, '_last_error_reason', '') or '未知错误')[:220]
        try:
            show_toast(f'新闻页面刷新失败！原因：{reason}', 'error', self)
        except Exception:
            pass

    def _show_error(self, message):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        card = QFrame()
        card.setStyleSheet(
            'QFrame { background:rgba(30,41,41,0.88); border:1px solid rgba(255,255,255,0.18);'
            ' border-radius:10px; } QLabel { background:transparent; }'
        )
        layout = QVBoxLayout(card)
        label = QLabel(f'暂时无法获取新闻。\n{message}')
        label.setWordWrap(True)
        label.setStyleSheet('color:#f4fbfb; font-size:13px;')
        layout.addWidget(label)
        retry = QPushButton('重试')
        retry.setFixedWidth(90)
        retry.clicked.connect(lambda: self.load(force=True))
        layout.addWidget(retry, 0, Qt.AlignLeft)
        self.cards_layout.addWidget(card)
        self.cards_layout.addStretch(1)

    def _render(self, items):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards = []
        versions = [item for item in items if item.get('kind') == 'version']
        for index, data in enumerate(versions):
            self.cards_layout.addWidget(
                NewsCard(data, self, expanded=(index == 0))
            )
            if index == 0 and len(versions) > 1:
                self.cards_layout.addWidget(NewsSection('历 史 版 本'))
        history = next((item for item in items if item.get('kind') == 'history'), None)
        if history:
            self.cards_layout.addWidget(HistoryCard(history))
        self.cards_layout.addStretch(1)
        self._cards = items

    def _prune_caches(self):
        '''限制新闻相关磁盘缓存，避免长期使用后无限增长。'''

        root = scl_dir() / 'cache'
        try:
            image_root = root / 'mcnews_images'
            dirs = [path for path in image_root.iterdir() if path.is_dir()]
            dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            keep = {self._news_signature or 'default'}
            kept = 0
            for path in dirs:
                if path.name in keep:
                    continue
                if kept < 2:
                    kept += 1
                    continue
                safe_rmtree(path)
        except Exception:
            pass
        for name, limit in (('mcnews_detail_images', 80), ('mcnews_details', 40)):
            try:
                folder = root / name
                files = [path for path in folder.iterdir() if path.is_file()]
                files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
                for path in files[limit:]:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            except Exception:
                continue

    def _image_cache_path(self, url):
        key = hashlib.sha1(str(url).encode('utf-8')).hexdigest()
        signature = self._news_signature or 'default'
        return scl_dir() / 'cache' / 'mcnews_images' / signature / key

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

    def open_version_detail(self, data):
        '''在主页内部切换到版本详情页，不使用独立窗口。'''

        data = dict(data or {})
        key = str(data.get('version') or data.get('title') or '')
        page = self._detail_pages.get(key)
        if page is None:
            page = VersionDetailPage(data, self.stack)
            page.back_requested.connect(self.show_news_home)
            self.stack.addWidget(page)
            self._detail_pages[key] = page
            while len(self._detail_pages) > 2:
                old_key = next(
                    (k for k, v in self._detail_pages.items() if v is not page),
                    None,
                )
                if old_key is None:
                    break
                old_page = self._detail_pages.pop(old_key)
                self.stack.removeWidget(old_page)
                try:
                    old_page.shutdown()
                except RuntimeError:
                    pass
                old_page.deleteLater()
        self.stack.setCurrentWidget(page)

    def show_news_home(self):
        self.stack.setCurrentWidget(self.news_view)

    def _release_detail_pages(self):
        if not self._detail_pages:
            return
        pages = list(self._detail_pages.values())
        self._detail_pages.clear()
        if self.stack.currentWidget() is not self.news_view:
            self.stack.setCurrentWidget(self.news_view)
        for page in pages:
            self.stack.removeWidget(page)
            try:
                page.shutdown()
            except RuntimeError:
                pass
            page.deleteLater()

    def request_image(self, label, urls):
        urls = [u for u in urls or [] if u]
        if not urls:
            label.setText('无新闻图片')
            return
        for url in urls:
            cached = self._cached_image(url)
            if cached is not None:
                label.set_source(cached)
                return
        self._next_image(label, urls)

    def _next_image(self, label, urls):
        if not urls:
            try:
                label.setText('新闻图片加载失败')
            except RuntimeError:
                pass
            return
        url = urls.pop(0)
        cached = self._cached_image(url)
        if cached is not None:
            label.set_source(cached)
            return
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'User-Agent', b'PMGL-Launcher/1.1')
        try:
            request.setTransferTimeout(8000)
        except Exception:
            pass
        reply = self._image_manager.get(request)
        try:
            if not bool(ConfigManager().get('verify_ssl', False)):
                reply.sslErrors.connect(lambda _errors, r=reply: r.ignoreSslErrors())
        except Exception:
            pass
        self._image_pending[reply] = (label, urls, url)

    def _on_image_finished(self, reply):
        label, urls, url = self._image_pending.pop(reply, (None, [], ''))
        if label is None:
            reply.deleteLater()
            return
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
                if data:
                    cache = self._image_cache_path(url)
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    if not cache.is_file():
                        cache.write_bytes(data)
                if isinstance(label, NewsImageLabel):
                    label.set_source(pixmap)
                else:
                    label.setPixmap(
                        pixmap.scaled(680, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                    label.setText('')
                return
            except RuntimeError:
                return
        try:
            self._next_image(label, urls)
        except RuntimeError:
            pass
