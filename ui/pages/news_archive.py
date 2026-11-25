"""新闻主页内置的历史版本 Archive 列表页。"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from pathlib import Path

import requests
from PySide6.QtCore import QRect, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from core.app_font import ui_font
from core.paths import scl_dir
from ui.widgets.back_button import BackButton
from ui.widgets.loading_overlay import LoadingOverlay

logger = logging.getLogger('NewsArchive')
NEWS_UA = 'PCL2/2.13'
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
CACHE_DIR = 'mcnews_help'
_DETACHED_WORKERS = set()


def _text(value):
    value = html.unescape(str(value or ''))
    return re.sub(r'\s+', ' ', value).strip()


def _attr(attrs, name):
    match = re.search(
        rf'\b{re.escape(name)}="([^"]*)"', attrs or '', re.I
    )
    return _text(match.group(1)) if match else ''


def _cache_paths(url):
    key = hashlib.sha1(str(url or '').encode('utf-8')).hexdigest()
    root = scl_dir() / 'cache' / CACHE_DIR
    return root / f'{key}.json', root / f'{key}.xaml'


def _xaml_url(json_url):
    url = str(json_url or '')
    return re.sub(r'(?i)\.json(?=($|[?#]))', '.xaml', url, count=1)


def parse_archive_xaml(text):
    """把 Archive XAML 解析成卡片分组和可点击条目。"""

    groups = []
    card_pattern = re.compile(
        r'<local:MyCard\b([^>]*)>(.*?)</local:MyCard>', re.S | re.I
    )
    item_pattern = re.compile(
        r'<local:MyListItem\b([^>]*?)/?>', re.S | re.I
    )

    def parse_items(body):
        items = []
        for match in item_pattern.finditer(body or ''):
            attrs = match.group(1)
            event_data = _attr(attrs, 'EventData')
            if not event_data:
                continue
            items.append({
                'title': _attr(attrs, 'Title'),
                'info': _attr(attrs, 'Info'),
                'event_type': _attr(attrs, 'EventType'),
                'event_data': event_data,
                'logo': _attr(attrs, 'Logo'),
                'tooltip': _attr(attrs, 'ToolTip'),
            })
        return items

    for match in card_pattern.finditer(str(text or '')):
        attrs, body = match.group(1), match.group(2)
        items = parse_items(body)
        if items:
            groups.append({
                'title': _attr(attrs, 'Title'),
                'items': items,
            })
    if not groups:
        items = parse_items(str(text or ''))
        if items:
            groups.append({'title': '', 'items': items})
    return groups


def _logo_pixmap(logo):
    name = str(logo or '').strip()
    if name.startswith('pack://'):
        stem = Path(name.replace('\\', '/')).stem.lower()
        mapping = {
            'grass': 'Release.png',
            'grasspath': 'Snapshoot.png',
            'grassblock': 'Release.png',
            'dirt': 'Release.png',
            'stone': 'Release.png',
            'cobblestone': 'Release.png',
        }
        filename = mapping.get(stem, 'Release.png')
        path = Path(__file__).resolve().parent.parent / 'imgs' / filename
        if path.is_file():
            return QPixmap(str(path))
    if name.startswith(('http://', 'https://')):
        # 图标不阻塞列表：网络图标使用统一默认图，避免破图。
        pass
    path = Path(__file__).resolve().parent.parent / 'imgs' / 'Release.png'
    return QPixmap(str(path)) if path.is_file() else QPixmap()


class ArchiveFetchWorker(QThread):
    done = Signal(str, str)
    error = Signal(str)

    def __init__(self, json_url, xaml_url, parent=None):
        super().__init__(parent)
        self.json_url = str(json_url)
        self.xaml_url = str(xaml_url or _xaml_url(json_url))
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _get(self, url):
        response = requests.get(
            url, headers={'User-Agent': NEWS_UA}, timeout=20
        )
        response.raise_for_status()
        raw = response.content
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise ValueError('Archive 内容过大')
        return raw.decode('utf-8', errors='replace')

    def run(self):
        try:
            json_text = self._get(self.json_url)
            if self._cancelled:
                return
            xaml_text = self._get(self.xaml_url)
            if not self._cancelled:
                self.done.emit(json_text, xaml_text)
        except Exception as exc:
            logger.warning('Archive 加载失败 %s', exc, exc_info=True)
            if not self._cancelled:
                self.error.emit(str(exc))


class ArchiveRow(QFrame):
    clicked = Signal(dict)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = dict(item or {})
        self.setObjectName('ArchiveRow')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            'QFrame#ArchiveRow { background: palette(base);'
            ' border:1px solid palette(mid); border-radius:8px; }'
            'QFrame#ArchiveRow:hover { background: palette(alternate-base);'
            ' border-color:#88DDDD; }'
        )
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setFixedSize(38, 38)
        icon.setAlignment(Qt.AlignCenter)
        pix = _logo_pixmap(self.item.get('logo'))
        if not pix.isNull():
            icon.setPixmap(
                pix.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        icon.setStyleSheet('background:transparent;')
        layout.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(str(self.item.get('title') or ''))
        title.setWordWrap(True)
        title.setStyleSheet('color: palette(text); font-size:14px; font-weight:700;')
        info = QLabel(str(self.item.get('info') or ''))
        info.setWordWrap(True)
        info.setStyleSheet('color: palette(mid); font-size:12px;')
        text.addWidget(title)
        if info.text().strip():
            text.addWidget(info)
        layout.addLayout(text, 1)
        self.setToolTip(str(self.item.get('tooltip') or ''))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ArchiveSection(QFrame):
    def __init__(self, title, items, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background:transparent;')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)
        if str(title or '').strip():
            label = QLabel(str(title))
            label.setStyleSheet(
                'color: palette(text); font-size:15px; font-weight:900;'
            )
            layout.addWidget(label)
        for item in items:
            row = ArchiveRow(item)
            layout.addWidget(row)
            setattr(row, '_archive_title', str(title or ''))
            row.setProperty('_archive_row', True)


class ArchiveListPage(QWidget):
    back_requested = Signal()
    open_archive = Signal(str, str)
    open_version = Signal(dict)

    def __init__(self, url, title='', parent=None):
        super().__init__(parent)
        self.url = str(url or '').strip()
        self.json_url = self.url
        self.xaml_url = _xaml_url(self.url)
        self._title_fallback = str(title or '')
        self._has_content = False
        self._worker = None
        self._closed = False
        self.setObjectName('NewsArchivePage')
        self.setStyleSheet('QWidget#NewsArchivePage { background:transparent; }')
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 18, 40, 24)
        root.setSpacing(8)
        header = QHBoxLayout()
        back = BackButton()
        back.clicked.connect(self._on_back)
        self.back_btn = back
        self.title_label = QLabel(self._title_fallback or '版本列表')
        self.title_label.setFont(ui_font(23, 700))
        self.title_label.setStyleSheet('color: palette(text);')
        header.addWidget(back)
        header.addWidget(self.title_label, 1)
        root.addLayout(header)
        self.error_row = QHBoxLayout()
        self.error_label = QLabel('')
        self.error_label.setStyleSheet('color:#ff8a8a; font-size:12px;')
        self.error_label.hide()
        self.retry_btn = QPushButton('重试')
        self.retry_btn.setStyleSheet(
            'QPushButton { background:transparent; color:#88DDDD;'
            ' border:1px solid rgba(136,221,221,0.55); border-radius:6px;'
            ' padding:3px 12px; }'
            'QPushButton:hover { background:rgba(136,221,221,0.18); }'
        )
        self.retry_btn.clicked.connect(self.load)
        self.retry_btn.hide()
        self.error_row.addWidget(self.error_label, 1)
        self.error_row.addWidget(self.retry_btn)
        root.addLayout(self.error_row)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet('QScrollArea { background:transparent; border:none; }')
        self.content = QWidget()
        self.content.setStyleSheet('background:transparent;')
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 4, 0, 24)
        self.content_layout.setSpacing(10)
        self.content_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)
        self.loading_overlay = LoadingOverlay(self.scroll.viewport())
        self.loading_overlay.attach_to(self.scroll.viewport())
        self.loading_overlay.retry_requested.connect(self.load)
        self._read_cache()
        self._start_fetch()

    def _on_back(self):
        if not self._closed:
            self.back_requested.emit()

    def _read_cache(self):
        json_path, xaml_path = _cache_paths(self.json_url)
        try:
            if not json_path.is_file() or not xaml_path.is_file():
                return
            self._apply(
                json_path.read_text(encoding='utf-8', errors='replace'),
                xaml_path.read_text(encoding='utf-8', errors='replace'),
                cached=True,
            )
        except OSError:
            pass

    def _start_fetch(self):
        if not self.json_url:
            self._show_error('缺少 Archive 地址')
            return
        old = self._worker
        if old is not None and old.isRunning():
            old.cancel()
        if not self._has_content:
            self.loading_overlay.start_auto('正在加载版本列表')
        self._worker = ArchiveFetchWorker(self.json_url, self.xaml_url, self)
        self._worker.done.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def load(self):
        if self._closed:
            return
        self.error_label.hide()
        self.retry_btn.hide()
        if not self._has_content:
            self.loading_overlay.start_auto('正在加载版本列表')
        self._start_fetch()

    def _on_loaded(self, json_text, xaml_text):
        if self._closed:
            return
        try:
            json_path, xaml_path = _cache_paths(self.json_url)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json_text, encoding='utf-8')
            xaml_path.write_text(xaml_text, encoding='utf-8')
        except OSError:
            pass
        self._apply(json_text, xaml_text, cached=False)

    def _apply(self, json_text, xaml_text, cached=False):
        try:
            data = json.loads(json_text)
        except (TypeError, ValueError):
            data = {}
        title = str(data.get('Title') or '').strip() if isinstance(data, dict) else ''
        if title:
            self.title_label.setText(title)
        groups = parse_archive_xaml(xaml_text)
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        entries = 0
        for group in groups:
            items = group.get('items') or []
            entries += len(items)
            section = ArchiveSection(group.get('title') or '', items)
            for row in section.findChildren(ArchiveRow):
                row.clicked.connect(self._open_item)
            self.content_layout.addWidget(section)
        if not entries:
            empty = QLabel('暂无版本记录')
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet('color: palette(mid); padding: 30px;')
            self.content_layout.addWidget(empty)
        self.content_layout.addStretch(1)
        self._has_content = bool(entries)
        self.error_label.hide()
        self.retry_btn.hide()
        self.loading_overlay.stop()
        if cached:
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def _open_item(self, item):
        event_type = str(item.get('event_type') or '')
        event_data = str(item.get('event_data') or '').strip()
        if not event_data:
            return
        if event_type == '打开网页':
            QDesktopServices.openUrl(QUrl(event_data))
            return
        if 'VersionDetail' in event_data:
            detail_url = event_data.replace(
                'VersionDetail.json', 'VersionDetail.xaml'
            )
            if '.json' in detail_url and '.xaml' not in detail_url:
                detail_url = _xaml_url(detail_url)
            self.open_version.emit({
                'title': str(item.get('title') or ''),
                'version': str(item.get('title') or ''),
                'detail_url': detail_url,
            })
            return
        if re.search(r'(?i)\.json(?:$|[?#])', event_data):
            self.open_archive.emit(event_data, str(item.get('title') or ''))

    def _on_error(self, message):
        if self._closed:
            return
        logger.warning('Archive 页面加载失败: %s', message)
        if self._has_content:
            self.error_label.setText('加载失败，当前显示缓存内容')
            self.error_label.show()
            self.retry_btn.show()
            self.loading_overlay.stop()
        else:
            self.loading_overlay.fail('加载失败，请点击重试')

    def _show_error(self, message):
        self._on_error(message)

    def shutdown(self):
        self._closed = True
        worker = self._worker
        self._worker = None
        if worker is not None:
            try:
                worker.cancel()
                if not worker.wait(500):
                    # 页面即将 deleteLater；不能把仍在运行的 QThread 留在
                    # 页面对象下，否则父对象析构会销毁运行中的线程。
                    worker.setParent(None)
                    _DETACHED_WORKERS.add(worker)
                    worker.finished.connect(
                        lambda w=worker: (
                            _DETACHED_WORKERS.discard(w), w.deleteLater()
                        )
                    )
                    if worker.isFinished():
                        _DETACHED_WORKERS.discard(worker)
                        worker.deleteLater()
            except RuntimeError:
                pass
        try:
            self.loading_overlay.stop()
        except RuntimeError:
            pass


__all__ = [
    'ArchiveListPage', 'ArchiveFetchWorker', 'parse_archive_xaml',
]
