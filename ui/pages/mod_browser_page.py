'''
社区资源下载页（通用）
支持 Modrinth / CurseForge 的 模组、整合包、数据包、资源包、光影包 浏览与下载。
特性：分页页码、完善筛选、异步图标加载（LRU 缓存）、线程安全清理。
'''

from __future__ import annotations
import html
import json
import logging
import math
import re
import threading
import time
import weakref
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote
from PySide6.QtCore import (Qt, Signal, QThread, QTimer, QUrl, QPoint, QSize, QObject, QEvent, QEasingCurve)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPalette, QPixmap)
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkDiskCache, QNetworkReply, QNetworkRequest)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QAbstractItemView, QProgressBar, QSizePolicy, QRadioButton, QButtonGroup, QScrollArea, QToolButton, QDialogButtonBox, QStackedWidget)
from ui.widgets.rounded_combo import RoundedComboBox
from ui.widgets.rounded_select import RoundedSelect, _SelectPopup
from ui.widgets.animated_list import AnimatedListView
from core.app_font import ui_font
from ui.widgets.back_button import BackButton
from ui.widgets.dark_dialog import DarkQuestionDialog
from ui.widgets.height_animation import HeightAnimation
from ui.widgets.loading_overlay import LoadingOverlay
from ui.widgets.toast import show_toast

logger = logging.getLogger('ModBrowser')
from ui.widgets.rotating_chevron import RotatingChevronButton
from ui.widgets.smooth_progress import SmoothProgressBar
from ui.design import IMAGE_PLACEHOLDER_DARK, IMAGE_PLACEHOLDER_LIGHT, MUTED_DARK, MUTED_LIGHT
from ui.widgets.painted_icons import painted_icon, set_button_icon
from core.config import ConfigManager
from core.download_manager import DownloadManager
from core.launch import LaunchManager
from core.mods import (LOADER_RELEVANT_TYPES, LOADERS, PROJECT_TYPES, SORTS, ModFile, ModProject, SearchResult, SourceUnavailable, _natural_key, cached_mc_versions as fetch_cached_mc_versions, download_candidates, fetch_mc_versions, get_source, installed_mod_tokens, normalize_mod_token)
from core.mod_translations import bilingual_html, bilingual_plain, translation_db
from ui.widgets.themed_text import themed_bilingual_html, themed_color
from core.net import (DownloadError, HttpClient, sanitize_filename, unique_path, is_network_error_text, network_available)
from core.paths import scl_dir
from ui.widgets.toast import notify_network_failure, show_toast
from core.version import VersionManager
PROJECT_TEMP = scl_dir() / 'temp_install'
PROJECT_TEMP.mkdir(parents=True, exist_ok=True)
_ACTIVE_WORKERS: 'set' = set()
_CATEGORY_CACHE: 'dict' = {}
_CATEGORY_LOADING: 'set' = set()
# MC百科没有公开的项目 ID 映射；首次点击时解析搜索页首条结果并缓存详情页。
_MCMOD_DETAIL_CACHE: 'dict' = {}

def _track_worker(worker):
    _ACTIVE_WORKERS.add(worker)
    def _done(w=worker):
        try:
            _ACTIVE_WORKERS.discard(w)
            w.deleteLater()
        except RuntimeError:
            pass

    worker.finished.connect(_done)
    worker.start()

class VersionComboBox(RoundedComboBox):
    '''可输入的游戏版本选择框：只有右侧箭头能展开列表。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self._arrow_open = False
        self._light = False
        self._arrow_btn = QToolButton(self)
        self._arrow_btn.setCursor(Qt.PointingHandCursor)
        self._arrow_btn.setFixedSize(22, 22)
        self._arrow_btn.setStyleSheet(
            'QToolButton { background:transparent; border:none; }'
            'QToolButton:hover { background:rgba(255,255,255,0.08);'
            ' border-radius:4px; }'
        )
        self._arrow_btn.clicked.connect(self._toggle_popup)
        self._custom_popup = _SelectPopup(self)
        self._custom_popup.item_chosen.connect(self._choose_popup_row)
        self._update_arrow()
        self.lineEdit().installEventFilter(self)
        self.view().setProperty('_theme_ignore', True)
        self._apply_view_style()

    def _apply_view_style(self):
        # 全局 QComboBox/QAbstractItemView 样式负责 popup 的圆角与深浅色，
        # 这里不能再设置局部样式，否则会覆盖 10px 外框圆角。
        self.view().setStyleSheet('')

    def set_light(self, light):
        self._light = bool(light)
        self._apply_view_style()
        self._update_arrow()

    def _update_arrow(self):
        set_button_icon(
            self._arrow_btn,
            'chevron_up' if self._arrow_open else 'chevron_down',
            14,
            '#555555' if self._light else '#cfd6e4',
        )

    def _toggle_popup(self):
        if self._custom_popup.isVisible():
            if time.monotonic() - getattr(self, '_opened_at', 0.0) < 0.28:
                return
            self.hidePopup()
        else:
            # 箭头按钮的 clicked 在鼠标释放时发出，等该事件结束再展开。
            QTimer.singleShot(0, self._open_popup_after_release)

    def _open_popup_after_release(self):
        if self.isVisible() and not self._custom_popup.isVisible():
            self.showPopup()

    def showPopup(self):
        if not self.isVisible() or not self.isEnabled():
            return
        items = [
            (self.itemText(index), index)
            for index in range(self.count())
        ]
        popup = self._custom_popup
        popup.list.setFont(self.font())
        popup.rebuild(items, self.currentIndex())
        size = popup.preferred_size(self.width())
        popup.setFixedSize(size)
        popup.layout().activate()
        if self.currentIndex() >= 10:
            item = popup.list.item(self.currentIndex())
            if item is not None:
                popup.list.scrollToItem(
                    item, QAbstractItemView.EnsureVisible
                )
        anchor = self.mapToGlobal(QPoint(0, self.height() + 2))
        top = self.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(anchor) or self.screen()
        area = screen.availableGeometry() if screen is not None else None
        x, y = anchor.x(), anchor.y()
        if area is not None:
            x = min(
                max(x, area.left()),
                max(area.left(), area.right() - size.width() + 1),
            )
            above = top.y() - size.height() - 2
            if y + size.height() > area.bottom() and above >= area.top():
                y = above
            else:
                y = min(
                    y, max(area.top(), area.bottom() - size.height() + 1)
                )
        slide = -5 if y >= top.y() else 5
        popup.prepare_open(QPoint(x, y), slide)
        popup.show()
        self._opened_at = time.monotonic()
        self._arrow_open = True
        self._update_arrow()

    def hidePopup(self):
        self._custom_popup.hide()
        self._arrow_open = False
        self._update_arrow()

    def _on_popup_hidden(self):
        self._arrow_open = False
        self._update_arrow()

    def _choose_popup_row(self, row):
        if 0 <= row < self.count():
            self.setCurrentIndex(row)
            self.setEditText(self.itemText(row))
            self.lineEdit().editingFinished.emit()
        self._custom_popup.hide()
        self.lineEdit().setFocus(Qt.OtherFocusReason)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrow_btn.move(
            max(0, self.width() - 25),
            max(0, (self.height() - self._arrow_btn.height()) // 2),
        )
        self._arrow_btn.raise_()

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() == QEvent.MouseButtonPress:
            if self._custom_popup.isVisible():
                self.hidePopup()
        return super().eventFilter(obj, event)

def _orphan_worker(worker):
    '''页面销毁但线程尚未结束时，把线程从对象树摘除并保持引用。'''

    try:
        worker.setParent(None)
    except Exception:
        pass
    _ACTIVE_WORKERS.add(worker)
    worker.finished.connect((lambda w=worker: _ACTIVE_WORKERS.discard(w)))

_CLEANUP_REGISTERED = False

def _shutdown_all_workers():
    '''应用退出前强制回收所有后台线程，避免 QThread 析构崩溃。'''

    workers = list(_ACTIVE_WORKERS)
    for worker in workers:
        cancel = getattr(worker, 'cancel', None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass
    for worker in workers:
        try:
            if worker.isRunning() and not worker.wait(300):
                worker.terminate()
                worker.wait(100)
        except RuntimeError:
            continue
    _ACTIVE_WORKERS.clear()

def _register_app_cleanup():
    global _CLEANUP_REGISTERED
    if _CLEANUP_REGISTERED:
        return
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(_shutdown_all_workers)
        _CLEANUP_REGISTERED = True

class SearchWorker(QThread):
    done = Signal(int, object)
    error = Signal(int, str)
    def __init__(self, generation, source_name, query, project_type, mc_version, loader, category, sort, offset, limit):
        super().__init__()
        self.generation = generation
        self.source_name = source_name
        self.query = query
        self.project_type = project_type
        self.mc_version = mc_version
        self.loader = loader
        self.category = category
        self.sort = sort
        self.offset = offset
        self.limit = limit
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                return
            translation_db.ensure_loaded(wait=3)
            search_query = self.query
            hints = []
            if re.search(r'[\u3400-\u9fff]', str(self.query or '')):
                hints = translation_db.search_hints(self.query, self.project_type, 6)
                if hints:
                    search_query = hints[0]['en']
            source = get_source(self.source_name)
            result = source.search(search_query, self.project_type, mc_version=self.mc_version, loader=self.loader, category=self.category, sort=self.sort, offset=self.offset, limit=self.limit)
            if not result.projects and hints and self.offset == 0:
                for hint in hints[1:4]:
                    if self._cancelled:
                        return
                    result = source.search(hint['en'], self.project_type, mc_version=self.mc_version, loader=self.loader, category=self.category, sort=self.sort, offset=self.offset, limit=self.limit)
                    if result.projects:
                        break
            if not self._cancelled:
                self.done.emit(self.generation, result)
            return
        except SourceUnavailable as exc:
            if not self._cancelled:
                self.error.emit(self.generation, str(exc))
                return
            return
        except Exception as exc:
            if not self._cancelled:
                self.error.emit(self.generation, f'搜索失败：{exc}')
                return
            return


class FilesWorker(QThread):
    done = Signal(list)
    error = Signal(str)
    def __init__(self, source_name, project, mc_version, loader):
        super().__init__()
        self.source_name = source_name
        self.project = project
        self.mc_version = mc_version
        self.loader = loader
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        source = None
        try:
            source = get_source(self.source_name)
            files = source.get_files(self.project, self.mc_version, self.loader)
            if not self._cancelled:
                self.done.emit(files)
            return
        except SourceUnavailable as exc:
            if not self._cancelled:
                self.error.emit(str(exc))
                return
            return
        except Exception as exc:
            if not self._cancelled:
                text = str(exc)
                if 'getaddrinfo failed' in text or 'NameResolutionError' in text:
                    text = '网络解析失败（DNS），请检查网络连接后重试。'
                self.error.emit(f'获取文件列表失败：{text}')
                return
            return


class DownloadWorker(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    done = Signal(str)
    error = Signal(str)
    def __init__(self, url, dest, sha1='', size=0, urls=None, sha512=''):
        super().__init__()
        self.url = url
        self.urls = list(urls) if urls else download_candidates(url)
        self.dest = Path(dest)
        self.sha1 = sha1
        self.sha512 = sha512
        self.size = size
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        http = HttpClient(timeout=15, retries=1)
        last_error = None
        try:
            for url in self.urls:
                if self._cancelled:
                    return
                label = 'Modrinth 加速节点' if 'cdn.modrinth.com' in url or 'cdn-raw.modrinth.com' in url else '下载源'
                self.status.emit(f'正在通过{label}下载 {self.dest.name}...')
                def progress(done, total):
                    self.progress.emit(int(done), int(total))
                try:
                    http.download(
                        url, self.dest,
                        sha1=self.sha1 or None,
                        sha512=self.sha512 or None,
                        size=self.size or None,
                        progress=progress,
                        cancel=(lambda: self._cancelled),
                    )
                    if self._cancelled:
                        return
                    self.done.emit(str(self.dest))
                    return
                except Exception as exc:
                    last_error = exc
                    if self._cancelled:
                        return
                    continue
        finally:
            http.close()
        if not self._cancelled:
            self.error.emit(f'下载失败：{last_error or "所有下载源均不可用"}')


class InstalledModsWorker(QThread):
    '''后台读取已安装 Mod 元数据，避免打开文件页时扫描 JAR 卡住界面。'''

    done = Signal(object)
    def __init__(self, mods_dir):
        super().__init__()
        self.mods_dir = Path(mods_dir)

    def run(self):
        try:
            tokens = installed_mod_tokens(self.mods_dir)
        except Exception:
            tokens = set()
        self.done.emit(tokens)


class VersionsWorker(QThread):
    done = Signal(object)
    def run(self):
        cached = []
        try:
            cached = fetch_cached_mc_versions(False)
        except Exception:
            cached = []
        if cached:
            self.done.emit(cached)
        try:
            versions = fetch_mc_versions(False, force=True)
            if versions and versions != cached:
                self.done.emit(versions)
        except Exception:
            if not cached:
                self.done.emit([])


class CategoriesWorker(QThread):
    done = Signal(str, str, object)
    error = Signal(str, str, str)
    def __init__(self, source_name, project_type):
        super().__init__()
        self.source_name = source_name
        self.project_type = project_type

    def run(self):
        try:
            source = get_source(self.source_name)
            categories = source.get_categories(self.project_type)
            self.done.emit(self.source_name, self.project_type, categories)
        except Exception as exc:
            self.error.emit(self.source_name, self.project_type, str(exc))


class IconLoader(QObject):
    ready = Signal(str, QPixmap)
    _fallback_ready = Signal(str, bytes)
    _fallback_failed = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._fallback_ready.connect(self._on_fallback_ready)
        self._fallback_failed.connect(self._on_fallback_failed)
        self._fallback_active = set()
        try:
            cache_dir = scl_dir() / 'icon_cache'
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache = QNetworkDiskCache(self)
            cache.setCacheDirectory(str(cache_dir))
            cache.setMaximumCacheSize(125829120)
            self._manager.setCache(cache)
        except Exception:
            pass
        self._cache = OrderedDict()
        self._queue = deque()
        self._queued = set()
        self._active = set()
        self._replies = {}
        self._max_active = 8
        self._fallback_max = 4
        self._cache_limit = 160
        self._enabled = True

    def set_enabled(self, enabled):
        self._enabled = enabled
        if enabled:
            self._process()
            return
        self._queue.clear()
        self._queued.clear()

    def cached(self, url):
        if url in self._cache:
            self._cache.move_to_end(url)
            return self._cache[url]

    def request(self, url):
        if not url or not self._enabled:
            return
        if url in self._cache or url in self._queued or url in self._active:
            return
        self._queue.append(url)
        self._queued.add(url)
        self._process()

    def _process(self):
        while self._enabled and self._queue and len(self._active) < self._max_active:
            url = self._queue.popleft()
            self._queued.discard(url)
            if url in self._cache or url in self._active:
                continue
            self._active.add(url)
            req = QNetworkRequest(QUrl(url))
            req.setRawHeader(b'User-Agent', b'PMGL-Launcher/1.1')
            try:
                req.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute, QNetworkRequest.CacheLoadControl.PreferCache)
                req.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, True)
                req.setPriority(QNetworkRequest.Priority.HighPriority)
                req.setTransferTimeout(6000)
            except Exception:
                pass
            reply = self._manager.get(req)
            try:
                if not bool(ConfigManager().get('verify_ssl', True)):
                    reply.sslErrors.connect(
                        lambda _errors, r=reply: r.ignoreSslErrors()
                    )
            except Exception:
                pass
            self._replies[reply] = url
            reply.finished.connect((lambda r=reply: self._on_finished(r)))
            if self._enabled and self._queue:
                pass

    def _on_finished(self, reply):
        url = self._replies.pop(reply, None)
        if url:
            self._active.discard(url)
        ok = False
        try:
            if reply.error() == QNetworkReply.NoError:
                data = reply.readAll()
                pix = QPixmap()
                if pix.loadFromData(data) and not pix.isNull() and url:
                    scaled = pix.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self._cache[url] = scaled
                    while len(self._cache) > self._cache_limit:
                        self._cache.popitem(last=False)
                    self.ready.emit(url, scaled)
                    ok = True
            if not ok and self._enabled and url:
                self._start_fallback(url)
        except RuntimeError:
            pass
        finally:
            reply.deleteLater()
            self._process()

    def _start_fallback(self, url):
        if not url or not self._enabled or url in self._fallback_active:
            return
        # 图标 CDN 失败时不要无限开线程；社区页几十个图标同时回退会卡主界面。
        if len(self._fallback_active) >= self._fallback_max:
            return
        self._fallback_active.add(url)
        threading.Thread(target=self._fetch_fallback, args=(url,), daemon=True).start()

    def _fetch_fallback(self, url):
        data = b''
        try:
            http = HttpClient(timeout=8, retries=1)
            try:
                response = http.get(url)
                if response.status_code < 400:
                    data = response.content or b''
            finally:
                http.close()
        except Exception:
            data = b''
        if data:
            try:
                self._fallback_ready.emit(url, data)
            except RuntimeError:
                pass
        else:
            try:
                self._fallback_failed.emit(url)
            except RuntimeError:
                pass

    def _on_fallback_ready(self, url, data):
        self._fallback_active.discard(url)
        if not self._enabled:
            return
        pix = QPixmap()
        if not pix.loadFromData(data) or pix.isNull():
            return
        scaled = pix.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._cache[url] = scaled
        while len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)
        self.ready.emit(url, scaled)

    def _on_fallback_failed(self, url):
        self._fallback_active.discard(url)

    def stop(self):
        self._enabled = False
        self._queue.clear()
        self._queued.clear()
        for reply in list(self._replies.keys()):
            try:
                reply.abort()
                reply.deleteLater()
            except RuntimeError:
                continue
        self._replies.clear()
        self._active.clear()


def _format_downloads(value):
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        count = 0
    if count >= 100000000:
        return f'{count / 100000000:.1f} 亿'
    if count >= 10000:
        return f'{count / 10000:.1f} 万'
    return f'{count:,}'


def _relative_update(value):
    text = str(value or '').strip()
    if not text:
        return '未知'
    try:
        date = datetime.strptime(text[:10], '%Y-%m-%d')
        days = max(0, (datetime.now() - date).days)
    except (TypeError, ValueError):
        return text
    if days <= 0:
        return '今天'
    if days < 30:
        return f'{days} 天前'
    if days < 365:
        return f'{max(1, days // 30)} 个月前'
    return f'{days // 365} 年前'


def _version_summary(versions):
    years = []
    for item in list(versions or [])[:20]:
        match = re.search(r'\d+\.\d+(?:\.\d+)?', str(item or ''))
        if match and match.group(0) not in years:
            years.append(match.group(0))
    if not years:
        return '全版本'
    suffix = '…' if len(years) > 3 else ''
    return ', '.join(years[:3]) + suffix


class _ElidedLabel(QLabel):
    '''单行省略标签：侧栏展开/窗口缩窄时保持文字不被裁掉。'''

    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._full_text = ''
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        self._full_text = ' '.join(str(text or '').split())
        self._apply_elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        width = self.width()
        if width <= 0:
            super().setText(self._full_text)
            return
        text = self.fontMetrics().elidedText(
            self._full_text, Qt.ElideRight, max(24, width)
        )
        super().setText(text)


class ProjectCard(QFrame):
    clicked = Signal(object)
    download_clicked = Signal(object)
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.icon_url = project.icon_url
        self._light = self.palette().color(QPalette.Window).lightness() > 128
        self.setObjectName('ProjectCard')
        self.setStyleSheet('\n            QFrame#ProjectCard {\n                background: rgba(30,41,41,0.84);\n                border: 1px solid rgba(255,255,255,0.16);\n                border-radius: 9px;\n            }\n            QFrame#ProjectCard:hover {\n                background: rgba(28,30,38,0.72);\n                border: 1px solid rgba(136,221,221,0.45);\n            }\n        ')
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(104)
        self.setToolTip('点击选择版本并下载')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 14, 9)
        layout.setSpacing(12)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(56, 56)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setText('?')
        self._apply_icon_placeholder()
        layout.addWidget(self.icon_label)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.RichText)
        self.title_label.setStyleSheet('background: transparent;')
        self._refresh_title()
        text_box.addWidget(self.title_label)
        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(5)
        tag_style = (
            'QLabel { background: rgba(255,255,255,0.10); color: rgba(255,255,255,0.58);'
            ' border-radius: 4px; padding: 1px 6px; font-size: 11px; }'
        )
        for category in list(project.categories or [])[:4]:
            if not category:
                continue
            tag = QLabel(str(category))
            tag.setStyleSheet(tag_style)
            summary_row.addWidget(tag, 0, Qt.AlignTop)
        self.desc_label = _ElidedLabel(' '.join(str(project.description or '暂无描述').split()))
        self.desc_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.desc_label.setFixedHeight(18)
        self.desc_label.setToolTip(project.description or '暂无描述')
        self.desc_label.setStyleSheet('color: rgba(255,255,255,0.55); font-size: 12px; background: transparent;')
        summary_row.addWidget(self.desc_label, 1)
        text_box.addLayout(summary_row)
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 1, 0, 0)
        meta_row.setSpacing(8)
        meta_style = 'color:rgba(255,255,255,0.42); font-size:11px; background:transparent;'
        self.version_label = QLabel(f'版本 {_version_summary(project.versions)}')
        self.download_label = QLabel(f'↓ {_format_downloads(project.downloads)}')
        self.updated_label = QLabel(f'↑ {_relative_update(project.updated)}')
        self.source_label = QLabel(f'◎ {project.source}')
        for label in (self.version_label, self.download_label, self.updated_label, self.source_label):
            label.setStyleSheet(meta_style)
        meta_row.addWidget(self.version_label)
        meta_row.addStretch(1)
        meta_row.addWidget(self.download_label)
        meta_row.addStretch(1)
        meta_row.addWidget(self.updated_label)
        meta_row.addStretch(1)
        meta_row.addWidget(self.source_label)
        text_box.addLayout(meta_row)
        layout.addLayout(text_box, 1)

    def _refresh_title(self):
        self.title_label.setText(themed_bilingual_html(
            self.project.translated_title, self.project.english_title, 13, 11
        ))
        self.title_label.setToolTip(
            bilingual_plain(
                self.project.translated_title, self.project.english_title
            )
        )

    def _apply_icon_placeholder(self):
        if not hasattr(self, 'icon_label'):
            return
        background = IMAGE_PLACEHOLDER_LIGHT if self._light else IMAGE_PLACEHOLDER_DARK
        foreground = MUTED_LIGHT if self._light else MUTED_DARK
        self.icon_label.setStyleSheet(
            f'background:{background}; border-radius:9px; color:{foreground};'
        )

    def set_light(self, light):
        self._light = bool(light)
        self._apply_icon_placeholder()
        self._refresh_title()

    def set_icon(self, pix):
        if pix is None or pix.isNull():
            return
        from ui.widgets.settings_widgets import hidpi_pixmap, rounded_pixmap
        size = self.icon_label.width() or 56
        pixmap = hidpi_pixmap(pix, size, size)
        self.icon_label.setPixmap(rounded_pixmap(pixmap, max(6, round(size * 0.2))))
        self.icon_label.setText('')

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.project)
        super().mouseReleaseEvent(event)


class PaginationBar(QWidget):
    page_changed = Signal(int)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_page = 1
        self.total_pages = 1
        self._buttons_layout = QHBoxLayout()
        self._buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._buttons_layout.setSpacing(4)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addStretch(1)
        self.prev_btn = QPushButton('上一页')
        self.prev_btn.setFixedHeight(28)
        self.prev_btn.clicked.connect((lambda: self._goto(self.current_page - 1)))
        self.next_btn = QPushButton('下一页')
        self.next_btn.setFixedHeight(28)
        self.next_btn.clicked.connect((lambda: self._goto(self.current_page + 1)))
        self.prev_btn.setStyleSheet(self._btn_style())
        self.next_btn.setStyleSheet(self._btn_style())
        layout.addWidget(self.prev_btn)
        layout.addLayout(self._buttons_layout)
        layout.addWidget(self.next_btn)
        self.jump_edit = QLineEdit()
        self.jump_edit.setFixedSize(48, 28)
        self.jump_edit.setPlaceholderText('页码')
        self.jump_edit.setStyleSheet('\n            QLineEdit {\n                background: rgba(255,255,255,0.06);\n                border: 1px solid rgba(255,255,255,0.12);\n                border-radius: 5px; color: #f0f0f0; padding: 2px 4px;\n            }\n        ')
        self.jump_edit.returnPressed.connect(self._on_jump)
        layout.addWidget(self.jump_edit)
        jump_btn = QPushButton('跳转')
        jump_btn.setFixedHeight(28)
        jump_btn.setStyleSheet(self._btn_style())
        jump_btn.clicked.connect(self._on_jump)
        layout.addWidget(jump_btn)
        layout.addStretch(1)
        self.setVisible(False)

    def _btn_style(self):
        return '\n            QPushButton {\n                background: rgba(255,255,255,0.06);\n                color: #ddd; border: none; border-radius: 5px;\n                padding: 3px 10px;\n            }\n            QPushButton:hover { background: rgba(255,255,255,0.12); }\n            QPushButton:disabled { color: #666; }\n        '

    def set_pages(self, current, total):
        self.current_page = max(1, current)
        self.total_pages = max(1, total)
        self.setVisible(self.total_pages > 1)
        self.prev_btn.setEnabled(self.current_page > 1)
        self.next_btn.setEnabled(self.current_page < self.total_pages)
        while self._buttons_layout.count():
            item = self._buttons_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        pages = self._visible_pages()
        for page in pages:
            if page is None:
                label = QLabel('…')
                label.setStyleSheet('color: #888; padding: 0 2px;')
                self._buttons_layout.addWidget(label)
                continue
            btn = QPushButton(str(page))
            btn.setFixedSize(30, 28)
            btn.setCheckable(True)
            btn.setChecked(page == self.current_page)
            btn.setStyleSheet('\n                QPushButton {\n                    background: rgba(255,255,255,0.06);\n                    color: #ddd; border: none; border-radius: 5px;\n                }\n                QPushButton:hover { background: rgba(255,255,255,0.14); }\n                QPushButton:checked { background: #88DDDD; color: white; }\n            ')
            btn.clicked.connect((
                lambda checked=False, p=page, b=btn:
                b.setChecked(True) if p == self.current_page else self._goto(p)
            ))
            self._buttons_layout.addWidget(btn)

    def _visible_pages(self):
        total = self.total_pages
        current = self.current_page
        if total <= 9:
            return list(range(1, total + 1))
        pages = [1]
        start = max(2, current - 2)
        end = min(total - 1, current + 2)
        if start > 2:
            pages.append(None)
        pages.extend(range(start, end + 1))
        if end < total - 1:
            pages.append(None)
        pages.append(total)
        return pages

    def _goto(self, page):
        page = max(1, min(self.total_pages, int(page)))
        if page != self.current_page:
            self.page_changed.emit(page)

    def _on_jump(self):
        try:
            page = int(self.jump_edit.text().strip())
        except ValueError:
            return
        self._goto(page)


class _SectionToggle(QToolButton):
    '''标题按钮右侧叠加可旋转 chevron，保持原有整行点击范围。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setArrowType(Qt.NoArrow)
        self.arrow = RotatingChevronButton(
            self, size=24, collapsed_angle=-90, expanded_angle=0,
            light_color='#2F6F6F', dark_color='#A8E6E6',
        )
        self.arrow.setToolTip('展开')
        self.arrow.clicked.connect(lambda *_: self.toggle())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrow.move(
            max(4, self.width() - 32),
            max(0, (self.height() - self.arrow.height()) // 2),
        )
        self.arrow.raise_()


class CollapsibleSection(QWidget):
    def __init__(self, title, subtitle='', highlighted=False, expanded=False, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self._toggle = _SectionToggle()
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        text = title + (f'    {subtitle}' if subtitle else '')
        self._toggle.setText(text)
        self._toggle.setCursor(Qt.PointingHandCursor)
        if highlighted:
            self._toggle.setStyleSheet('\n                QToolButton {\n                    text-align: left; padding: 8px 34px 8px 10px; border: none;\n                    border-left: 3px solid #88DDDD; border-radius: 6px;\n                    background: rgba(136,221,221,0.18); color: #A8E6E6;\n                    font-weight:900; font-size: 13px;\n                }\n                QToolButton:hover { background: rgba(136,221,221,0.28); }\n            ')
        else:
            self._toggle.setStyleSheet('\n                QToolButton {\n                    text-align: left; padding: 8px 34px 8px 10px; border: none;\n                    border-radius: 6px; background: rgba(255,255,255,0.045);\n                    color: rgba(255,255,255,0.85); font-size: 13px;\n                }\n                QToolButton:hover { background: rgba(255,255,255,0.09); }\n            ')
        outer.addWidget(self._toggle)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(18, 4, 4, 8)
        self._content_layout.setSpacing(4)
        outer.addWidget(self._content)
        self._height_anim = HeightAnimation(self._content, self)
        self._height_anim.setDuration(160)
        self._height_anim.finished.connect(self._on_height_anim_finished)
        self._expanded = bool(expanded)
        self._toggle.toggled.connect(self._on_toggled)
        self._apply_expanded(expanded, animate=False)

    def _on_toggled(self, checked):
        self._apply_expanded(checked, animate=True)

    def _apply_expanded(self, checked, animate=True):
        self._expanded = bool(checked)
        self._toggle.arrow.set_expanded(self._expanded, animate=animate)
        self._toggle.arrow.setToolTip('收起' if self._expanded else '展开')
        self._height_anim.stop()
        content = self._content
        if not animate:
            if self._expanded:
                content.setMaximumHeight(16777215)
                content.setVisible(True)
            else:
                content.setMaximumHeight(0)
                content.setVisible(False)
            return
        if self._expanded:
            content.setVisible(True)
            start = content.maximumHeight()
            if start >= 16777215 or start < 0:
                start = max(0, content.height())
            target = max(
                content.sizeHint().height(),
                self._content_layout.sizeHint().height(),
            )
            if target <= 0:
                target = content.height()
            content.setMaximumHeight(start)
            self._height_anim.setDuration(160)
            self._height_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._height_anim.setStartValue(start)
            self._height_anim.setEndValue(max(start, target))
        else:
            start = content.height()
            if start <= 0:
                start = content.maximumHeight()
            if start >= 16777215:
                start = max(0, content.sizeHint().height())
            content.setMaximumHeight(start)
            self._height_anim.setDuration(160)
            self._height_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._height_anim.setStartValue(start)
            self._height_anim.setEndValue(0)
        self._height_anim.start()

    def _on_height_anim_finished(self):
        if self._expanded:
            self._content.setMaximumHeight(16777215)
        else:
            self._content.setMaximumHeight(0)
            self._content.setVisible(False)

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)

    def set_expanded(self, expanded):
        self._toggle.setChecked(expanded)


def _release_badge_pixmap(kind, size=22):
    kind = str(kind or 'release').lower()
    if kind == 'beta':
        letter, color = 'B', QColor('#d18a2f')
    elif kind == 'alpha':
        letter, color = 'A', QColor('#3f8fc7')
    else:
        letter, color = 'R', QColor('#75b93f')
    pix = QPixmap(max(16, int(size)), max(16, int(size)))
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    w = h = pix.width()
    cx = w / 2.0
    top = QPainterPath()
    top.moveTo(cx, 0.8)
    top.lineTo(w - 1.2, h * 0.28)
    top.lineTo(cx, h * 0.53)
    top.lineTo(1.2, h * 0.28)
    top.closeSubpath()
    left = QPainterPath()
    left.moveTo(1.2, h * 0.28)
    left.lineTo(cx, h * 0.53)
    left.lineTo(cx, h - 1.0)
    left.lineTo(1.2, h * 0.76)
    left.closeSubpath()
    right = QPainterPath()
    right.moveTo(cx, h * 0.53)
    right.lineTo(w - 1.2, h * 0.28)
    right.lineTo(w - 1.2, h * 0.76)
    right.lineTo(cx, h - 1.0)
    right.closeSubpath()
    painter.fillPath(top, color.lighter(132))
    painter.fillPath(left, color)
    painter.fillPath(right, color.darker(122))
    painter.setPen(Qt.NoPen)
    painter.setPen(QColor('#ffffff'))
    font = painter.font()
    font.setPixelSize(max(9, int(w * 0.48)))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignCenter, letter)
    painter.end()
    return pix


class FileRow(QWidget):
    clicked = Signal(object)
    def __init__(self, mod_file, current_mc='', project_title='', project_translated='', parent=None):
        super().__init__(parent)
        self.mod_file = mod_file
        self._selected = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
        release = str(mod_file.release_type or 'release').lower()
        badge = QLabel()
        badge.setFixedSize(22, 22)
        badge.setPixmap(_release_badge_pixmap(release, 22))
        badge.setToolTip({'release': '正式版', 'beta': '测试版', 'alpha': '早期测试版'}.get(release, release))
        layout.addWidget(badge, 0, Qt.AlignTop)
        style = ''
        try:
            style = ConfigManager().get('mod_manage_style', 'title_translated')
        except Exception:
            pass
        if style == 'title_filename':
            main_title = mod_file.filename
            detail_title = bilingual_plain(project_translated, project_title) or mod_file.display_name or ''
        else:
            main_title = None
            detail_title = mod_file.filename
        box = QVBoxLayout()
        box.setSpacing(1)
        self.title = QLabel()
        self.title.setTextFormat(Qt.RichText)
        self.title.setStyleSheet('background: transparent;')
        self.title.setWordWrap(True)
        self._row_main_title = main_title
        self._row_detail_title = detail_title
        self._row_project_translated = project_translated
        self._row_project_title = project_title
        self._refresh_title()
        box.addWidget(self.title)
        versions = ', '.join(mod_file.game_versions[:6])
        meta = f'{mod_file.display_name or detail_title}  ·  {detail_title}  ·  {mod_file.release_type}  ·  {versions or "版本未知"}'
        if mod_file.date:
            meta += f'  ·  {mod_file.date}'
        if mod_file.size:
            meta += f'  ·  {mod_file.size / 1024 / 1024:.1f} MB'
        self.meta_label = QLabel(meta)
        self.meta_label.setToolTip(meta)
        self.meta_label.setStyleSheet('color: rgba(255,255,255,0.45); font-size: 11px; background: transparent;')
        self.meta_label.setWordWrap(True)
        box.addWidget(self.meta_label)
        layout.addLayout(box, 1)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName('FileRow')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._apply_style()

    def _refresh_title(self):
        if self._row_main_title is not None:
            self.title.setText(
                f'<span style="font-size:13px;color:{themed_color()};">'
                f'{html.escape(str(self._row_main_title))}</span>'
            )
            self.title.setToolTip(str(self._row_main_title))
            return
        full_title = bilingual_plain(
            self._row_project_translated,
            self._row_project_title or self._row_detail_title,
        )
        self.title.setText(themed_bilingual_html(
            self._row_project_translated,
            self._row_project_title or self._row_detail_title,
            13, 11,
        ))
        self.title.setToolTip(full_title)

    def set_light(self, light):
        self._refresh_title()

    def _apply_style(self):
        if self._selected:
            self.setStyleSheet('\n                QWidget#FileRow { background: rgba(136,221,221,0.30);\n                          border: 1px solid #3399ff; border-radius: 6px; }\n            ')
            return
        self.setStyleSheet('\n                QWidget#FileRow { background: rgba(18,20,26,0.5);\n                          border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; }\n                QWidget#FileRow:hover { background: rgba(28,30,38,0.72);\n                                border: 1px solid rgba(136,221,221,0.4); }\n            ')

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._apply_style()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self)
        super().mouseReleaseEvent(event)


def _is_base_api_project(project):
    """Fabric API / Quilt API 这类基础 API 独立成组。"""

    values = (
        getattr(project, 'slug', ''),
        getattr(project, 'project_id', ''),
        getattr(project, 'english_title', ''),
        getattr(project, 'title', ''),
        getattr(project, 'translated_title', ''),
    )
    text = ' '.join(str(value or '') for value in values).lower()
    compact = text.replace(' ', '').replace('_', '-')
    return any(
        key in compact
        for key in ('fabric-api', 'quilt-api', 'fabricapi', 'quiltapi')
    )


def _dep_key(dep):
    return str(dep.get('project_id') or dep.get('modId') or dep.get('projectId') or dep.get('version_id') or dep.get('file_name') or '')

class DependencyInfoWorker(QThread):
    done = Signal(int, list)
    def __init__(self, generation, source_name, dependencies):
        super().__init__()
        self.generation = generation
        self.source_name = source_name
        self.dependencies = list(dependencies)

    def run(self):
        def fetch_dependency(dep):
            key = _dep_key(dep)
            try:
                project = get_source(self.source_name).resolve_dependency(dep)
            except Exception:
                project = None
            if project is None:
                name = dep.get('file_name') or key or '未命名前置'
                project = ModProject(source=self.source_name, project_id=key or name, slug='', title=name, description='未能获取前置模组详情', project_type='mod')
            return key, project

        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=6) as pool:
                resolved = list(pool.map(fetch_dependency, self.dependencies))
        except Exception:
            resolved = []
        result = [(key, project, b'') for key, project in resolved]
        self.done.emit(self.generation, result)


class DependencyCard(QFrame):
    clicked = Signal(object)
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self._light = self.palette().color(QPalette.Window).lightness() > 128
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName('DependencyCard')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setToolTip('点击跳转并下载此前置模组')
        self._last_activation = 0.0
        self.setStyleSheet('\n            QFrame#DependencyCard { background: transparent; border: none; }\n            QFrame#DependencyCard:hover { background: rgba(255,255,255,0.045);\n                                          border: none; border-radius: 6px; }\n        ')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)
        self.icon = QLabel('?')
        self.icon.setFixedSize(40, 40)
        self.icon.setAlignment(Qt.AlignCenter)
        self._apply_icon_placeholder()
        layout.addWidget(self.icon)
        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel()
        title.setTextFormat(Qt.RichText)
        title.setStyleSheet('background:transparent;')
        self.title_label = title
        self._refresh_title()
        text.addWidget(title)
        sub = QLabel(project.source)
        sub.setStyleSheet('color:rgba(255,255,255,0.45); font-size:11px; background:transparent;')
        text.addWidget(sub)
        layout.addLayout(text, 1)

    def _refresh_title(self):
        self.title_label.setText(themed_bilingual_html(
            self.project.translated_title, self.project.english_title, 13, 11
        ))

    def _apply_icon_placeholder(self):
        if not hasattr(self, 'icon'):
            return
        background = IMAGE_PLACEHOLDER_LIGHT if self._light else IMAGE_PLACEHOLDER_DARK
        foreground = MUTED_LIGHT if self._light else MUTED_DARK
        self.icon.setStyleSheet(
            f'background:{background}; border:none; border-radius:9px; color:{foreground};'
        )

    def set_light(self, light):
        self._light = bool(light)
        self._apply_icon_placeholder()
        self._refresh_title()

    def set_icon(self, pix):
        if pix and not pix.isNull():
            from ui.widgets.settings_widgets import hidpi_pixmap, rounded_pixmap
            pixmap = hidpi_pixmap(pix, 40, 40)
            self.icon.setPixmap(rounded_pixmap(pixmap, 9))
            self.icon.setText('')

    def _activate(self):
        now = time.monotonic()
        if now - self._last_activation < 0.35:
            return
        self._last_activation = now
        self.clicked.emit(self.project)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._activate()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._activate()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ProjectFilesDialog(QDialog):
    dependency_selected = Signal(object)
    def __init__(self, project, mc_version, loader, game_dir, project_type, parent=None, embedded=False, preferred_source=''):
        super().__init__(parent)
        self.project = project
        self.mc_version = mc_version
        self.loader = loader
        self.game_dir = Path(game_dir)
        self.project_type = project_type
        self.preferred_source = str(preferred_source or '').strip()
        # 整合包自带游戏版本，不能按当前实例版本过滤其文件列表。
        self._files_mc_version = '' if project_type == 'modpack' else mc_version
        self._files_loader = '' if project_type == 'modpack' else loader
        self.embedded = embedded
        self._cleaned = False
        self._mcmod_net = None
        self._worker = None
        self._files = []
        self._file_rows = []
        self._selected_row = None
        self._install_dir_override = None
        self._dep_generation = 0
        self._dep_worker = None
        self._dep_workers = set()
        self._dep_cache = {}
        self._dep_order = []
        self._dep_types = {}
        self._dep_cached_items = {}
        self._dep_items = []
        self._installed_mod_tokens = set()
        self._installed_worker = None
        self._dep_icon_loader = IconLoader(self)
        self._dep_icon_loader.ready.connect(self._on_dep_icon_ready)
        self._dep_cards = []
        if embedded:
            self.setWindowFlags(Qt.Widget)
            self.setObjectName('ProjectFilesPage')
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.setStyleSheet('QDialog#ProjectFilesPage { background: transparent; color: #f0f0f0; }')
        else:
            self.setWindowTitle(f'选择版本 - {bilingual_plain(project.translated_title, project.english_title)}')
            self.setWindowIcon(QIcon())
            self.setMinimumSize(820, 640)
            self.setStyleSheet('background-color: #1e1e1e; color: #f0f0f0;')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        title_row = QHBoxLayout()
        if embedded:
            back_btn = BackButton()
            back_btn.clicked.connect(self.reject)
            title_row.addWidget(back_btn)
        title = QLabel()
        title.setTextFormat(Qt.RichText)
        title.setText(themed_bilingual_html(project.translated_title, project.english_title, 16, 12))
        title.setToolTip(
            bilingual_plain(project.translated_title, project.english_title)
        )
        self.header_title_label = title
        title_row.addWidget(title)
        title_row.addStretch(1)
        layout.addLayout(title_row)
        sub = QLabel(f'{project.source} · {project.description}')
        sub.setWordWrap(True)
        sub.setStyleSheet('color: rgba(255,255,255,0.6);')
        layout.addWidget(sub)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        source_name = str(getattr(project, 'source', '') or '').strip()
        # “全部”不是具体来源：结果卡片仍保留它实际来自 Modrinth/CurseForge。
        if source_name.lower() in ('', 'all', '全部', '全部来源'):
            source_name = self.preferred_source
        if source_name.lower() in ('', 'all', '全部', '全部来源'):
            source_name = 'CurseForge'
        self._source_name = source_name
        is_mod = str(
            getattr(project, 'project_type', '') or project_type or 'mod'
        ).lower() == 'mod'
        source_style = (
            'QPushButton { background:transparent; color:#f0a030; border:1px solid #c47b1d;'
            ' border-radius:6px; padding:6px 12px; }'
            'QPushButton:hover { background:rgba(240,160,48,0.16); }'
            if 'curseforge' in source_name.lower() else
            'QPushButton { background:transparent; color:#9ac35a; border:1px solid #719b3d;'
            ' border-radius:6px; padding:6px 12px; }'
            'QPushButton:hover { background:rgba(120,170,60,0.16); }'
        )
        self.source_btn = QPushButton(f'转到 {source_name}')
        self.source_btn.setStyleSheet(source_style)
        self.source_btn.clicked.connect(self._open_source)
        self.mcmod_btn = QPushButton('转到 MC 百科')
        self.mcmod_btn.setStyleSheet(
            'QPushButton { background:transparent; color:#e8e8e8; border:1px solid rgba(255,255,255,0.30);'
            ' border-radius:6px; padding:6px 12px; }'
            'QPushButton:hover { background:rgba(255,255,255,0.10); }'
        )
        self.mcmod_btn.clicked.connect(self._open_mcmod)
        self.mcmod_btn.setVisible(is_mod)
        self.copy_name_btn = QPushButton('复制名称')
        self.copy_name_btn.setStyleSheet(
            'QPushButton { background:transparent; color:#e8e8e8; border:1px solid rgba(255,255,255,0.30);'
            ' border-radius:6px; padding:6px 12px; }'
            'QPushButton:hover { background:rgba(255,255,255,0.10); }'
        )
        self.copy_name_btn.clicked.connect(self._copy_name)
        action_row.addWidget(self.source_btn, 1)
        if is_mod:
            action_row.addWidget(self.mcmod_btn, 1)
        action_row.addWidget(self.copy_name_btn, 1)
        layout.addLayout(action_row)
        self.warning_label = QLabel('')
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet('\n            QLabel { background: rgba(255,180,80,0.18); color: #ffcc80;\n                     border-left: 3px solid #f0a030; padding: 7px 10px;\n                     font-size: 12px; }\n        ')
        self.warning_label.hide()
        layout.addWidget(self.warning_label)
        self.dep_panel = QFrame()
        self.dep_panel.setObjectName('DependencyPanel')
        self.dep_panel.setAttribute(Qt.WA_StyledBackground, True)
        self.dep_panel.setStyleSheet('\n            QFrame#DependencyPanel { background: rgba(30,41,41,0.84);\n                     border: 1px solid rgba(255,255,255,0.22);\n                     border-radius: 8px; }\n            QFrame#DependencyPanel QLabel { background: transparent; border: none; }\n        ')
        dep_layout = QVBoxLayout(self.dep_panel)
        dep_layout.setContentsMargins(10, 8, 10, 8)
        dep_layout.setSpacing(6)
        self.dep_title = QLabel('此模组的前置模组')
        self.dep_title.setStyleSheet('color:rgba(255,255,255,0.78); font-size:13px; font-weight:900;')
        dep_layout.addWidget(self.dep_title)
        self.dep_list_layout = QVBoxLayout()
        self.dep_list_layout.setSpacing(5)
        dep_layout.addLayout(self.dep_list_layout)
        self.dep_panel.hide()
        layout.addWidget(self.dep_panel)
        self.status_label = QLabel('正在加载版本列表...')
        self.status_label.setStyleSheet('color: #f0a030; font-size: 12px;')
        layout.addWidget(self.status_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet('\n            QScrollArea { background: rgba(10,12,18,0.35); border:1px solid rgba(255,255,255,0.12);\n                          border-radius: 8px; }\n            QScrollBar:vertical { background: #2a2a2a; width: 10px; border-radius: 5px; }\n            QScrollBar::handle:vertical { background: #555; border-radius: 5px; min-height: 20px; }\n        ')
        self.groups_widget = QWidget()
        self.groups_layout = QVBoxLayout(self.groups_widget)
        self.groups_layout.setContentsMargins(8, 8, 8, 8)
        self.groups_layout.setSpacing(4)
        self.groups_layout.addStretch(1)
        self.scroll.setWidget(self.groups_widget)
        layout.addWidget(self.scroll, 1)
        self.world_combo = None
        self._datapack_dir = self.game_dir
        if project_type != 'modpack' and project_type != 'datapack':
            target_row = QHBoxLayout()
            target_row.addWidget(QLabel('安装到'))
            self.install_target_label = _ElidedLabel('')
            self.install_target_label.setStyleSheet(
                'color:rgba(255,255,255,0.56); font-size:12px;'
            )
            target_row.addWidget(self.install_target_label, 1)
            target_btn = QPushButton('更改')
            target_btn.setStyleSheet(
                'QPushButton { background:#3a3a3a; color:white; border:none;'
                ' border-radius:6px; padding:4px 10px; }'
                'QPushButton:hover { background:#4a4a4a; }'
            )
            target_btn.clicked.connect(self._choose_install_dir)
            target_row.addWidget(target_btn)
            layout.addLayout(target_row)
            self._refresh_install_target()
        if project_type == 'datapack':
            dest_row = QHBoxLayout()
            dest_row.addWidget(QLabel('保存到'))
            self.datapack_dir_label = QLabel(str(self._datapack_dir))
            self.datapack_dir_label.setWordWrap(True)
            self.datapack_dir_label.setStyleSheet(
                'color:rgba(255,255,255,0.62); font-size:12px;'
            )
            self.datapack_dir_label.setToolTip(
                '数据包默认保存为 zip 到游戏目录；'
                '选择 saves/<世界>/datapacks 可直接在对应世界中启用。'
            )
            choose_btn = QPushButton('选择文件夹...')
            choose_btn.setStyleSheet(
                'QPushButton { background:#3a3a3a; color:white; border:none;'
                ' border-radius:6px; padding:5px 10px; }'
                'QPushButton:hover { background:#4a4a4a; }'
            )
            choose_btn.clicked.connect(self._choose_datapack_dir)
            dest_row.addWidget(self.datapack_dir_label, 1)
            dest_row.addWidget(choose_btn)
            layout.addLayout(dest_row)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.download_btn = QPushButton('下一步')
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet('\n            QPushButton { background: #90D9D9; color: #122828; border: none;\n                          border-radius: 6px; padding: 8px 22px; }\n            QPushButton:hover { background: #78CACA; }\n            QPushButton:disabled { background: #333; color: #777; }\n        ')
        self.download_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton('返回' if embedded else '取消')
        cancel_btn.setStyleSheet('\n            QPushButton { background: #3a3a3a; color: white; border: none;\n                          border-radius: 6px; padding: 8px 22px; }\n            QPushButton:hover { background: #4a4a4a; }\n        ')
        cancel_btn.clicked.connect(self.reject)
        self.retry_btn = QPushButton('重试')
        self.retry_btn.setStyleSheet('\n            QPushButton { background: #f0a030; color: white; border: none;\n                          border-radius: 6px; padding: 8px 22px; }\n            QPushButton:hover { background: #d08020; }\n        ')
        self.retry_btn.hide()
        self.retry_btn.clicked.connect(self._retry_load)
        btn_row.addWidget(self.retry_btn)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self.download_btn)
        layout.addLayout(btn_row)
        self.loading_overlay = LoadingOverlay(self.scroll.viewport())
        self.loading_overlay.attach_to(self.scroll.viewport())
        self.loading_overlay.retry_requested.connect(self._retry_load)
        self.status_label.setText('')
        self._load_timeout = QTimer(self)
        self._load_timeout.setSingleShot(True)
        self._load_timeout.setInterval(20000)
        self._load_timeout.timeout.connect(self._on_load_timeout)
        self._load_timeout.start()
        self.loading_overlay.start_auto('正在加载文件列表')
        self._worker = FilesWorker(project.source, project, self._files_mc_version, self._files_loader)
        self._worker.done.connect(self._on_files)
        self._worker.error.connect(self._on_error)
        _track_worker(self._worker)
        if project_type == 'mod':
            installed = InstalledModsWorker(self.game_dir / 'mods')
            installed.done.connect(self._on_installed_mods)
            installed.finished.connect(
                (lambda w=installed: self._on_installed_worker_done(w))
            )
            self._installed_worker = installed
            _track_worker(installed)

    def _on_installed_mods(self, tokens):
        self._installed_mod_tokens = set(tokens or ())
        if self._dep_items:
            self._render_dependency_cards(self._dep_items)

    def _on_installed_worker_done(self, worker):
        if self._installed_worker is worker:
            self._installed_worker = None

    def _dependency_installed(self, project):
        if not self._installed_mod_tokens:
            return False
        candidates = [
            project.slug,
            project.project_id,
            project.english_title,
            project.title,
        ]
        for value in candidates:
            token = normalize_mod_token(value)
            if len(token) >= 3 and token in self._installed_mod_tokens:
                return True
        return False

    def set_light(self, light):
        label = getattr(self, 'header_title_label', None)
        if label is not None:
            label.setText(themed_bilingual_html(
                self.project.translated_title, self.project.english_title, 16, 12
            ))

    def _combo_style(self):
        return '\n            QComboBox {\n                background: rgba(255,255,255,0.06);\n                border: 1px solid rgba(255,255,255,0.12);\n                border-radius: 6px; color: #f0f0f0; padding: 4px 8px;\n            }\n            QComboBox::drop-down { border: none; }\n            \n        '

    @staticmethod
    def _release_order(kind):
        return {'release': 0, 'beta': 1, 'alpha': 2}.get((kind or '').lower(), 3)

    @staticmethod
    def _mc_versions(file):
        return [v for v in file.game_versions if re.match('^\\d+\\.\\d+', str(v))]

    def _file_supports_target(self, file):
        target = str(self._files_mc_version or '').strip()
        if not target:
            return True
        return target in self._mc_versions(file)

    def _is_compatible(self, file):
        if not self.mc_version:
            return True
        return str(self.mc_version) in self._mc_versions(file)

    @staticmethod
    def _file_matches_project_type(filename, project_type):
        suffix = Path(str(filename or '')).suffix.lower()
        if project_type == 'datapack':
            return suffix == '.zip'
        if project_type in ('resourcepack', 'shader'):
            return suffix == '.zip'
        if project_type == 'mod':
            return suffix in ('.jar', '.litemod')
        if project_type == 'modpack':
            return suffix in ('.mrpack', '.zip')
        return True

    def _file_matches_loader(self, file):
        if self.project_type != 'mod' or not self._files_loader:
            return True
        loaders = [
            str(value or '').strip().lower()
            for value in (getattr(file, 'loaders', None) or [])
        ]
        # 来源没有返回 loader 字段时不要误杀；有字段时按目标加载器本地筛选。
        return not loaders or self._files_loader.lower() in loaders

    def _on_files(self, files):
        try:
            self._load_timeout.stop()
        except (AttributeError, RuntimeError):
            pass
        # Modrinth 的数据包项目会同时返回 .jar（Mod）和 .zip（数据包）
        # 版本；按项目类型过滤，避免把 .jar 放进 world/datapacks。
        filtered = [
            file for file in (files or [])
            if (
                self._file_matches_project_type(
                    getattr(file, 'filename', ''), self.project_type
                )
                and self._file_matches_loader(file)
            )
        ]
        files = filtered
        self._files = files or []
        self.retry_btn.hide()
        self.download_btn.setEnabled(False)
        while self.groups_layout.count():
            item = self.groups_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._file_rows.clear()
        self._selected_row = None
        self._reset_file_scroll()
        is_modpack = self.project_type == 'modpack'
        target_version = self._files_mc_version
        if not files:
            if is_modpack:
                message = '未找到可用的整合包文件。'
            elif self.project_type == 'datapack':
                message = '未找到可用的数据包文件（需要 .zip）。'
            elif self.project_type == 'resourcepack':
                message = '未找到可用的资源包文件（需要 .zip）。'
            elif self.project_type == 'shader':
                message = '未找到可用的光影包文件（需要 .zip）。'
            else:
                message = '此 Mod 与当前游戏版本不适配或没有可用文件。'
                if target_version:
                    message = f'此 Mod 与当前游戏版本 {target_version} 不适配，未找到可用文件。'
            self.warning_label.setText(message)
            self.warning_label.show()
            self.status_label.setText('没有可用文件')
            self.status_label.setStyleSheet('color: #ff7777; font-size: 12px;')
            self.download_btn.setEnabled(False)
            self.loading_overlay.stop()
            return
        try:
            groups = {}
            for file in files:
                versions = self._mc_versions(file)
                if target_version and target_version in versions:
                    # 快照/正式版必须完整匹配，不能再把 26.4-snapshot-10
                    # 截成 26.4 后误判为兼容。
                    key = target_version
                elif versions:
                    key = sorted(versions, key=_natural_key)[-1]
                else:
                    key = '未知版本'
                groups.setdefault(key, []).append(file)
        except Exception as exc:
            logger.warning('文件列表解析失败: %s', exc, exc_info=True)
            self.warning_label.setText('文件列表解析失败，请点击重试')
            self.warning_label.show()
            self.status_label.setText('加载失败')
            self.status_label.setStyleSheet('color: #ff7777; font-size: 12px;')
            self.retry_btn.show()
            self.download_btn.setEnabled(False)
            self.loading_overlay.stop()
            return
        snapshot_target = 'snapshot' in str(target_version or '').lower()
        compatible = True if is_modpack else any(
            self._file_supports_target(file) for file in files
        )
        if not is_modpack and target_version and not compatible:
            self.warning_label.setText(f'此 Mod 与当前游戏版本 {target_version} 不适配。\n你仍然可以在下方选择其他游戏版本的 Mod 文件手动安装。')
            self.warning_label.show()
        else:
            self.warning_label.hide()
        keys = list(groups.keys())
        keys.sort(key=_natural_key, reverse=True)
        if (
            compatible and target_version and target_version in keys
            and not snapshot_target
        ):
            keys.remove(target_version)
            keys.insert(0, target_version)
        for key in keys:
            group_files = groups[key]
            group_files.sort(key=(lambda f: f.date or ''), reverse=True)
            group_files.sort(key=(lambda f: self._release_order(f.release_type)))
            highlighted = bool(
                target_version and key == target_version and not snapshot_target
            )
            section = CollapsibleSection(
                f'Minecraft {key}',
                f'{len(group_files)} 个文件'
                + (' · 当前游戏版本' if highlighted else ''),
                highlighted=highlighted,
                expanded=True if snapshot_target else (
                    highlighted
                    or key == keys[0] and (not target_version or not compatible)
                ),
            )
            for mod_file in group_files:
                row = FileRow(mod_file, target_version, self.project.english_title, self.project.translated_title)
                row.clicked.connect(self._select_row)
                section.add_widget(row)
                self._file_rows.append(row)
            self.groups_layout.addWidget(section)
        self.groups_layout.addStretch(1)
        self.download_btn.setEnabled(False)
        if is_modpack:
            self.status_label.setText(f'共 {len(files)} 个整合包版本；请选择文件后继续')
        elif target_version and snapshot_target:
            self.status_label.setText(
                f'共 {len(files)} 个文件；请选择文件后继续'
            )
        elif target_version:
            self.status_label.setText(f'共 {len(files)} 个文件；当前游戏版本 {target_version}' + ('，已找到适配文件' if compatible else '，未找到精确适配文件') + '；请选择文件后继续')
        else:
            self.status_label.setText(f'共 {len(files)} 个文件；请选择文件后继续')
        self.status_label.setStyleSheet(
            f'color: {"#7FE08A" if compatible else "#F0C060"}; font-size: 12px;'
        )
        self._reset_file_scroll()
        QTimer.singleShot(0, self._reset_file_scroll)
        self.loading_overlay.stop()

    def _on_error(self, message):
        text = str(message)
        logger.warning('文件列表加载失败: %s', text)
        network_problem = is_network_error_text(text) or not network_available()
        if network_problem:
            reason = '未连接到互联网，无法获取文件列表'
        elif 'getaddrinfo failed' in text or 'NameResolutionError' in text:
            reason = '网络解析失败（DNS），请检查网络后重试'
        else:
            reason = '加载失败，请点击重试'
        self.warning_label.hide()
        self.status_label.setText('')
        try:
            self._load_timeout.stop()
        except (AttributeError, RuntimeError):
            pass
        self.retry_btn.show()
        self.loading_overlay.fail(reason)

    def _on_load_timeout(self):
        worker = self._worker
        if worker is not None and worker.isRunning():
            try:
                worker.cancel()
            except Exception:
                pass
        if self.loading_overlay.is_loading():
            self.retry_btn.show()
            self.loading_overlay.fail('网络较慢，加载文件列表超时，请点击重试')

    def _retry_load(self):
        self.retry_btn.hide()
        self.warning_label.hide()
        self.status_label.setText('')
        try:
            self._load_timeout.start()
        except (AttributeError, RuntimeError):
            pass
        self.loading_overlay.start_auto('正在加载文件列表')
        self._worker = FilesWorker(self.project.source, self.project, self._files_mc_version, self._files_loader)
        self._worker.done.connect(self._on_files)
        self._worker.error.connect(self._on_error)
        _track_worker(self._worker)

    def _clear_dependency_cards(self):
        while self.dep_list_layout.count():
            item = self.dep_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._dep_cards.clear()

    def _show_dependencies(self, mod_file):
        self._dep_items = []
        self._clear_dependency_cards()
        dependencies = []
        dep_types = {}
        seen = set()
        for dep in mod_file.dependencies or []:
            if not isinstance(dep, dict):
                continue
            dtype = str(dep.get('dependency_type') or 'required').lower()
            if dtype not in ('required', 'optional'):
                continue
            pid = dep.get('project_id') or dep.get('modId') or dep.get('projectId')
            if pid and str(pid) == str(self.project.project_id):
                continue
            key = _dep_key(dep)
            if not key:
                continue
            if key in seen:
                if dtype == 'required':
                    dep_types[key] = 'required'
                continue
            seen.add(key)
            dependencies.append(dep)
            dep_types[key] = dtype
        if not dependencies:
            self._render_dependency_cards([])
            return
        self.dep_panel.show()
        self._dep_order = [_dep_key(dep) for dep in dependencies]
        self._dep_types = dep_types
        cached_items = [
            (key, *self._dep_cache[key])
            for key in self._dep_order
            if key in self._dep_cache
        ]
        missing = [
            dep for dep in dependencies if _dep_key(dep) not in self._dep_cache
        ]
        required_count = sum(
            1 for key in self._dep_order if dep_types.get(key) == 'required'
        )
        optional_count = len(self._dep_order) - required_count
        counts = f'必需 {required_count}'
        if optional_count:
            counts += f' · 可选 {optional_count}'
        if missing:
            self._dep_cached_items = {
                key: self._dep_cache[key]
                for key in self._dep_order
                if key in self._dep_cache
            }
            self.dep_title.setText(f'前置与可选模组（{counts}）· 正在加载...')
            self._dep_generation += 1
            gen = self._dep_generation
            worker = DependencyInfoWorker(gen, self.project.source, missing)
            worker.done.connect(self._on_dep_info)
            worker.finished.connect(worker.deleteLater)
            worker.finished.connect((lambda w=worker: self._dep_workers.discard(w)))
            self._dep_workers.add(worker)
            self._dep_worker = worker
            worker.start()
            return
        self.dep_title.setText(f'前置与可选模组（{counts}）')
        self._render_dependency_cards(cached_items)

    def _on_dep_info(self, generation, items):
        if generation != self._dep_generation:
            return
        merged = dict(self._dep_cached_items)
        for key, project, icon in items:
            merged[key] = project, icon
        self._dep_cache.update(merged)
        ordered = [
            (key, merged[key][0], merged[key][1])
            for key in self._dep_order
            if key in merged
        ]
        self._render_dependency_cards(ordered)

    def _render_dependency_cards(self, items):
        '''必需/可选依赖分组展示；基础 API 单独置顶，可选组默认折叠。'''

        self._dep_items = list(items or [])
        self._clear_dependency_cards()
        missing = []
        for item in self._dep_items:
            if len(item) >= 3:
                key, project, icon_bytes = item[0], item[1], item[2]
            else:
                key, project, icon_bytes = '', item[0], item[1]
            if not self._dependency_installed(project):
                missing.append((key, project, icon_bytes))
        if not missing:
            self.dep_panel.hide()
            return
        self.dep_title.setText(f'前置与可选模组（{len(missing)}）')
        self.dep_panel.show()
        groups = {'api': [], 'required': [], 'optional': []}
        for key, project, icon_bytes in missing:
            dtype = self._dep_types.get(key, 'required')
            if _is_base_api_project(project):
                groups['api'].append((project, icon_bytes))
            elif dtype == 'optional':
                groups['optional'].append((project, icon_bytes))
            else:
                groups['required'].append((project, icon_bytes))
        sections = (
            ('api', '基础 API', True),
            ('required', '必需前置', True),
            ('optional', '可选依赖', False),
        )
        for group_key, title, expanded in sections:
            group = groups.get(group_key) or []
            if not group:
                continue
            section = CollapsibleSection(
                title,
                f'{len(group)} 个',
                highlighted=group_key == 'api',
                expanded=expanded,
            )
            self.dep_list_layout.addWidget(section)
            for project, icon_bytes in group:
                card = DependencyCard(project)
                card.clicked.connect(self.dependency_selected.emit)
                section.add_widget(card)
                self._dep_cards.append(card)
                if icon_bytes:
                    pix = QPixmap()
                    if pix.loadFromData(icon_bytes) and not pix.isNull():
                        card.set_icon(pix)
                    continue
                if project.icon_url:
                    cached = self._dep_icon_loader.cached(project.icon_url)
                    if cached:
                        card.set_icon(cached)
                    else:
                        self._dep_icon_loader.request(project.icon_url)

    def _on_dep_icon_ready(self, url, pix):
        for card in self._dep_cards:
            if card.project.icon_url == url:
                card.set_icon(pix)

    def _reset_file_scroll(self):
        try:
            self.scroll.verticalScrollBar().setValue(0)
        except (AttributeError, RuntimeError):
            pass

    def _select_row(self, row):
        if row is None:
            return
        if self._selected_row is not None and self._selected_row is not row:
            self._selected_row.set_selected(False)
        self._selected_row = row
        row.set_selected(True)
        mod_file = getattr(row, 'mod_file', None)
        self.download_btn.setEnabled(
            bool(mod_file is not None and getattr(mod_file, 'download_url', ''))
        )
        self._show_dependencies(row.mod_file)

    def selected_file(self):
        if self._selected_row is not None:
            return self._selected_row.mod_file

    def selected_world(self):
        if self.project_type == 'datapack':
            return str(self._datapack_dir or self.game_dir)
        return ''

    def _refresh_install_target(self):
        label = getattr(self, 'install_target_label', None)
        if label is None:
            return
        target = self.install_dir()
        text = str(target) if target else '整合包导入器'
        label.setText(f'将安装到：{text}')
        label.setToolTip(text)

    def install_dir(self):
        if self.project_type == 'modpack':
            return None
        if self._install_dir_override:
            return Path(self._install_dir_override)
        if self.project_type == 'datapack':
            return Path(self._datapack_dir or self.game_dir)
        folder = {
            'resourcepack': 'resourcepacks',
            'shader': 'shaderpacks',
        }.get(self.project_type, 'mods')
        return Path(self.game_dir) / folder

    def _choose_install_dir(self):
        current = self.install_dir()
        path = QFileDialog.getExistingDirectory(
            self, '选择安装目录', str(current or self.game_dir)
        )
        if not path:
            return
        self._install_dir_override = Path(path)
        self._refresh_install_target()

    def _choose_datapack_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, '选择数据包保存文件夹', str(self._datapack_dir or self.game_dir)
        )
        if not path:
            return
        chosen = Path(path)
        # 兼容直接选择世界文件夹：游戏实际读取的位置是 <world>/datapacks。
        if (chosen / 'level.dat').is_file():
            chosen = chosen / 'datapacks'
        self._datapack_dir = chosen
        self.datapack_dir_label.setText(str(chosen))
        self._refresh_install_target()

    def _open_source(self):
        '''按项目实际来源打开详情页；来源为“全部”时使用当前项目来源。'''

        project = self.project
        project_type = str(
            getattr(project, 'project_type', '') or self.project_type or 'mod'
        ).lower()
        source = str(
            getattr(self, '_source_name', '') or getattr(project, 'source', '') or ''
        ).strip()
        source_lower = source.lower()
        slug = str(getattr(project, 'slug', '') or '').strip()
        url = ''
        if 'curseforge' in source_lower and slug:
            sections = {
                'mod': 'mc-mods', 'modpack': 'modpacks',
                'resourcepack': 'texture-packs', 'shader': 'shaders',
                'datapack': 'data-packs',
            }
            url = (
                'https://www.curseforge.com/minecraft/'
                f'{sections.get(project_type, "mc-mods")}/{quote(slug)}'
            )
        elif 'modrinth' in source_lower and slug:
            url = f'https://modrinth.com/{project_type}/{quote(slug)}'
        if not url:
            url = str(getattr(project, 'website_url', '') or '').strip()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _open_modrinth(self):
        # 兼容旧调用：现在统一按项目来源打开。
        self._open_source()

    @staticmethod
    def _mcmod_text(value):
        return re.sub(
            r'\s+', ' ',
            html.unescape(re.sub(r'<[^>]+>', '', str(value or ''))),
        ).strip()

    @classmethod
    def _mcmod_norm(cls, value):
        return re.sub(
            r'[\s\-_:/\\（）()\[\]【】]+', '',
            cls._mcmod_text(value),
        ).casefold()

    @classmethod
    def _mcmod_detail_from_html(cls, text, queries):
        '''从 MC百科搜索结果中挑选标题最匹配的详情页。'''

        source = str(text or '')
        starts = [m.start() for m in re.finditer(r'<div class="result-item">', source)]
        best_url = ''
        best_score = 0
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(source)
            item = source[start:end]
            match = re.search(
                r'<div class="head">.*?<a[^>]+href="(https?://(?:www\.)?mcmod\.cn/class/\d+\.html)"[^>]*>(.*?)</a>',
                item, re.S,
            )
            if not match:
                continue
            url = match.group(1)
            title = cls._mcmod_text(match.group(2))
            title_norm = cls._mcmod_norm(title)
            if not title_norm:
                continue
            for query in queries:
                query_norm = cls._mcmod_norm(query)
                if not query_norm:
                    continue
                if query_norm == title_norm:
                    score = 100
                elif query_norm in title_norm:
                    score = 50 + min(20, len(query_norm))
                elif title_norm in query_norm:
                    score = 30 + min(10, len(title_norm))
                else:
                    score = 0
                if score > best_score:
                    best_score = score
                    best_url = url
        return best_url

    def _open_mcmod(self):
        '''先解析 MC百科搜索页的首条匹配结果，再直接打开项目详情页。

        PCL 通过内置 WikiEntries 的 WikiId 直接拼
        https://www.mcmod.cn/class/{id}.html；这里采用等价的按需解析，
        避免随包携带大型映射表，同时保证最终打开的是详情页而非搜索页。
        '''

        project = self.project
        queries = []
        for value in (
            getattr(project, 'translated_title', ''),
            getattr(project, 'english_title', ''),
            getattr(project, 'title', ''),
        ):
            value = str(value or '').strip()
            if value and value not in queries:
                queries.append(value)
        if not queries:
            return
        cache_key = self._mcmod_norm(queries[0])
        cached = _MCMOD_DETAIL_CACHE.get(cache_key)
        if cached:
            QDesktopServices.openUrl(QUrl(cached))
            return
        search_url = f'https://search.mcmod.cn/s?key={quote(queries[0])}'
        if self._mcmod_net is None:
            self._mcmod_net = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(search_url))
        request.setRawHeader(
            b'User-Agent',
            b'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        )
        reply = self._mcmod_net.get(request)
        self.mcmod_btn.setEnabled(False)
        self.mcmod_btn.setText('正在查找 MC 百科...')
        reply.finished.connect(
            lambda r=reply, u=search_url, q=list(queries), k=cache_key:
            self._on_mcmod_search(r, u, q, k)
        )  # noqa: E731

    def _on_mcmod_search(self, reply, search_url, queries, cache_key):
        data = b''
        try:
            if reply.error() == QNetworkReply.NoError:
                data = bytes(reply.readAll())
        except RuntimeError:
            data = b''
        finally:
            try:
                reply.deleteLater()
            except RuntimeError:
                pass
        detail_url = ''
        try:
            detail_url = self._mcmod_detail_from_html(
                data.decode('utf-8', 'replace'), queries
            )
        except Exception:
            detail_url = ''
        if detail_url:
            _MCMOD_DETAIL_CACHE[cache_key] = detail_url
        try:
            self.mcmod_btn.setEnabled(True)
            self.mcmod_btn.setText('转到 MC 百科')
        except RuntimeError:
            pass
        # 找不到匹配详情时才回退搜索页，避免打开错误项目。
        QDesktopServices.openUrl(QUrl(detail_url or search_url))

    def _copy_name(self):
        name = bilingual_plain(self.project.translated_title, self.project.english_title) or self.project.title
        QApplication.clipboard().setText(str(name))
        show_toast(f'已复制名称：{name}', 'success', self, 2600)

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        try:
            self.loading_overlay.stop()
        except Exception:
            pass
        if self._worker is not None:
            worker = self._worker
            self._worker = None
            try:
                worker.cancel()
                if not worker.wait(300):
                    worker.terminate()
                    worker.wait(100)
            except Exception:
                pass
        try:
            self._dep_icon_loader.stop()
        except Exception:
            pass
        for worker in list(self._dep_workers):
            try:
                if worker.isRunning() and not worker.wait(250):
                    worker.terminate()
                    worker.wait(100)
            except RuntimeError:
                continue
        self._dep_workers.clear()
        installed = self._installed_worker
        self._installed_worker = None
        if installed is not None:
            try:
                if installed.isRunning() and not installed.wait(400):
                    installed.terminate()
                    installed.wait(100)
            except RuntimeError:
                pass

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)


class ModBrowserPage(QWidget):
    import_pack_requested = Signal(str, str, str)
    download_game_requested = Signal()
    PAGE_SIZE = 20
    def __init__(self, project_type, parent=None):
        super().__init__(parent)
        self.project_type = project_type
        self.setObjectName(f'ModBrowserPage_{project_type}')
        self._generation = 0
        self._page = 1
        self._total = 0
        self._cards = []
        self._last_projects = []
        self._loaded_once = False
        self._versions_loaded = False
        self._categories = {}
        self._categories_loaded = set()
        self._categories_loading = set()
        self._download_contexts = {}
        self._download_tasks = {}
        self._download_current = None
        self._download_status_level = 'normal'
        self._workers = []
        self._files_page = None
        self.config = ConfigManager()
        self.launch_mgr = LaunchManager()
        self.version_mgr = VersionManager()
        self.icon_loader = IconLoader(self)
        self.icon_loader.ready.connect(self._on_icon_ready)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.search)
        self._search_worker = None
        self._render_token = 0
        self._search_timeout = QTimer(self)
        self._search_timeout.setSingleShot(True)
        self._search_timeout.setInterval(15000)
        self._search_timeout.timeout.connect(self._on_search_timeout)
        self._init_ui()
        _register_app_cleanup()

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        browser = QWidget()
        self._browser_widget = browser
        layout = QVBoxLayout(browser)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(10)
        top = QHBoxLayout()
        title = QLabel(f'{PROJECT_TYPES.get(self.project_type, "资源")}下载')
        title.setFont(ui_font(20, QFont.DemiBold))
        title.setStyleSheet('color: #f0f0f0;')
        top.addWidget(title)
        top.addStretch(1)
        refresh_btn = QPushButton('刷新')
        refresh_btn.setStyleSheet(self._secondary_style())
        refresh_btn.clicked.connect(self.search)
        top.addWidget(refresh_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addLayout(top)
        filters = QFrame()
        filters.setStyleSheet('QFrame { background: rgba(30,41,41,0.84); border: 1px solid rgba(255,255,255,0.18); border-radius: 8px; }')
        filters.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        filters_layout = QVBoxLayout(filters)
        filters_layout.setContentsMargins(10, 8, 10, 8)
        filters_layout.setSpacing(6)

        # 第一行：搜索框独占，整合包页把本地安装按钮固定在右侧。
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('搜索名称、描述、作者...')
        self.search_input.setMinimumHeight(34)
        self.search_input.setMinimumWidth(180)
        self.search_input.setStyleSheet(
            'QLineEdit { background: rgba(255,255,255,0.06);'
            ' border: 1px solid rgba(255,255,255,0.12); border-radius: 8px;'
            ' color: #f0f0f0; padding: 5px 10px; }'
        )
        self.search_input.returnPressed.connect(self._trigger_search)
        search_row.addWidget(self.search_input, 1)
        self.source_combo = RoundedSelect()
        self.source_combo.addItems(['Modrinth', 'CurseForge'])
        self.source_combo.setMinimumWidth(0)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        search_row.addWidget(self.source_combo, 0)
        self.sort_combo = RoundedSelect()
        for key, label in SORTS.items():
            self.sort_combo.addItem(label, key)
        self.sort_combo.setMinimumWidth(0)
        self.sort_combo.currentIndexChanged.connect(self._trigger_search)
        search_row.addWidget(self.sort_combo, 0)
        if self.project_type == 'modpack':
            install_btn = QPushButton('安装已有整合包')
            install_btn.setFixedHeight(34)
            install_btn.setMinimumWidth(120)
            install_btn.setStyleSheet(self._secondary_style())
            install_btn.setToolTip('选择本地的 .mrpack 或 CurseForge 整合包 .zip')
            install_btn.clicked.connect(self._choose_local_pack)
            search_row.addWidget(install_btn, 0, Qt.AlignRight)
        filters_layout.addLayout(search_row)

        # 第二行：版本/加载器/分类/名称匹配弹性撑满，搜索和重置贴右。
        second_row = QHBoxLayout()
        second_row.setContentsMargins(0, 0, 0, 0)
        second_row.setSpacing(8)
        self.mc_combo = VersionComboBox()
        self.mc_combo.setEditable(True)
        self.mc_combo.setInsertPolicy(QComboBox.NoInsert)
        self.mc_combo.setMinimumWidth(190)
        self.mc_combo.setMinimumHeight(34)
        self.mc_combo.setMaxVisibleItems(20)
        self.mc_combo.setToolTip('默认全部版本，也可以直接输入版本号')
        self.mc_combo.addItem('全部（也可自行输入）', '')
        self.mc_combo.setCurrentIndex(-1)
        self.mc_combo.lineEdit().setPlaceholderText('全部（也可自行输入）')
        self.mc_combo.view().setMinimumWidth(190)
        self.mc_combo.setStyleSheet(
            self._combo_style()
            + 'QComboBox { padding-right: 30px; }'
            + 'QComboBox::drop-down { width: 0px; border: none; }'
            + 'QComboBox::down-arrow { image: none; }'
        )
        self.mc_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.mc_combo.lineEdit().editingFinished.connect(self._trigger_search)
        second_row.addWidget(self.mc_combo, 3)
        self.loader_combo = RoundedSelect()
        self.loader_combo.addItem('全部加载器', '')
        for key, label in LOADERS.items():
            self.loader_combo.addItem(label, key)
        self.loader_combo.currentIndexChanged.connect(self._trigger_search)
        second_row.addWidget(self.loader_combo, 2)
        self.category_combo = RoundedSelect()
        self.category_combo.addItem('全部分类', '')
        self.category_combo.currentIndexChanged.connect(self._trigger_search)
        second_row.addWidget(self.category_combo, 2)
        self.name_match_combo = RoundedSelect()
        self.name_match_combo.addItem('名称包含', 'contains')
        self.name_match_combo.addItem('名称开头', 'startswith')
        self.name_match_combo.setToolTip('仅过滤当前页结果')
        self.name_match_combo.currentIndexChanged.connect(self._render_current)
        second_row.addWidget(self.name_match_combo, 2)
        search_btn = QPushButton('搜索')
        search_btn.setFixedSize(105, 34)
        search_btn.setStyleSheet(self._primary_style())
        search_btn.clicked.connect(self._trigger_search)
        second_row.addWidget(search_btn, 0)
        reset_btn = QPushButton('重置条件')
        reset_btn.setFixedSize(115, 34)
        reset_btn.setStyleSheet(self._secondary_style())
        reset_btn.clicked.connect(self.reset_filters)
        second_row.addWidget(reset_btn, 0)
        for combo in (
            self.mc_combo, self.loader_combo,
            self.category_combo, self.name_match_combo,
        ):
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            combo.setMinimumWidth(0)
        for index, stretch in enumerate((3, 2, 2, 2)):
            second_row.setStretch(index, stretch)
        filters_layout.addLayout(second_row)
        layout.addWidget(filters)
        self.loader_combo.setEnabled(
            self.project_type in LOADER_RELEVANT_TYPES
        )
        self.results_list = AnimatedListView()
        self.results_list.setSpacing(4)
        self.results_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        layout.addWidget(self.results_list, 1)
        self.loading_overlay = LoadingOverlay(self.results_list.viewport())
        self.loading_overlay.attach_to(self.results_list.viewport())
        self.loading_overlay.retry_requested.connect(self._retry_search)
        bottom = QHBoxLayout()
        self.status_label = QLabel('正在加载...')
        self.status_label.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
        bottom.addWidget(self.status_label, 1)
        self.pagination = PaginationBar()
        self.pagination.page_changed.connect(self._on_page_changed)
        bottom.addWidget(self.pagination, 2)
        layout.addLayout(bottom)
        self.download_bar = SmoothProgressBar()
        self.download_bar.setRange(0, 100)
        self.download_bar.reset_value()
        self.download_bar.hide()
        self.download_bar.setStyleSheet('\n            QProgressBar { background: #333; border-radius: 6px; height: 12px; }\n            QProgressBar::chunk { background: #88DDDD; border-radius: 6px; }\n        ')
        layout.addWidget(self.download_bar)
        self._browser_index = self.stack.addWidget(browser)
        self.stack.setCurrentWidget(browser)

    def _combo_style(self):
        return '\n            QComboBox {\n                background: rgba(255,255,255,0.06);\n                border: 1px solid rgba(255,255,255,0.12);\n                border-radius: 8px; color: #f0f0f0; padding: 5px 10px;\n            }\n            QComboBox::drop-down { border: none; }\n            \n        '

    def _primary_style(self):
        return 'QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:8px; padding:6px 16px; } QPushButton:hover { background:#78CACA; }'

    def _secondary_style(self):
        return 'QPushButton { background:#3a3a3a; color:white; border:none; border-radius:8px; padding:6px 12px; } QPushButton:hover { background:#4a4a4a; }'

    def activate(self):
        if not self._versions_loaded:
            worker = VersionsWorker()
            worker.done.connect(self._on_versions)
            _track_worker(worker)
            self._versions_loaded = True
        if not self._loaded_once:
            self._loaded_once = True
            self.search()
        elif not self._cards and self._last_projects:
            # 离页时卡片已销毁，但搜索结果仍保留；回来时直接重建，不再请求网络。
            self._render_projects(self._last_projects)
        self.icon_loader.set_enabled(True)
        for card in self._cards:
            pix = card.icon_label.pixmap()
            if card.icon_url and (pix is None or pix.isNull()):
                self.icon_loader.request(card.icon_url)

    def _on_versions(self, versions):
        self._versions_loaded = bool(versions)
        if not versions:
            versions = [
                '26.3', '26.2', '26.1', '1.21.11', '1.21.10', '1.21.8',
                '1.21.4', '1.21.1', '1.20.6', '1.20.4', '1.20.1',
                '1.19.4', '1.19.2', '1.18.2', '1.17.1', '1.16.5',
                '1.15.2', '1.14.4', '1.12.2', '1.11.2', '1.10.2',
                '1.9.4', '1.8.9', '1.7.10',
            ]
        typed = self.mc_combo.currentText().strip()
        self.mc_combo.blockSignals(True)
        self.mc_combo.clear()
        self.mc_combo.addItem('全部（也可自行输入）', '')
        for vid in versions or []:
            text = str(vid).strip()
            if not re.fullmatch('\\d+\\.\\d+(?:\\.\\d+)?', text):
                continue
            self.mc_combo.addItem(text, text)
        if typed and typed not in ('全部', '全部（也可自行输入）', '全部(也可自行输入)'):
            idx = self.mc_combo.findText(typed)
            if idx >= 0:
                self.mc_combo.setCurrentIndex(idx)
            else:
                self.mc_combo.setCurrentIndex(-1)
                self.mc_combo.setEditText(typed)
        else:
            self.mc_combo.setCurrentIndex(-1)
            self.mc_combo.setEditText('')
        self.mc_combo.blockSignals(False)
        # 这里只更新版本下拉框，不要再触发搜索：页面激活时已经发起过一次搜索，
        # 版本列表稍后返回会再搜一次，导致列表闪一下并重复请求。

    def _on_target_changed(self):
        pass

    def _on_source_changed(self):
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem('全部分类', '')
        self.category_combo.blockSignals(False)
        self._load_categories()
        self._trigger_search()

    def _load_categories(self):
        source_name = self.source_combo.currentText()
        key = source_name, self.project_type
        if key in _CATEGORY_CACHE:
            self._categories[key] = list(_CATEGORY_CACHE[key])
            self._categories_loaded.add(key)
            self._populate_categories()
            return
        if key in self._categories_loaded:
            self._populate_categories()
            return
        if key in self._categories_loading or key in _CATEGORY_LOADING:
            return
        self._categories_loading.add(key)
        _CATEGORY_LOADING.add(key)
        worker = CategoriesWorker(source_name, self.project_type)
        worker.done.connect(self._on_categories)
        worker.error.connect(self._on_categories_error)
        worker.finished.connect(
            (lambda k=key: _CATEGORY_LOADING.discard(k))
        )
        _track_worker(worker)

    def _on_categories(self, source_name, project_type, categories):
        key = source_name, project_type
        self._categories[key] = categories or []
        _CATEGORY_CACHE[key] = list(categories or [])
        self._categories_loaded.add(key)
        self._categories_loading.discard(key)
        _CATEGORY_LOADING.discard(key)
        if source_name == self.source_combo.currentText() and project_type == self.project_type:
            self._populate_categories()

    def _on_categories_error(self, source_name, project_type, message):
        key = source_name, project_type
        self._categories_loading.discard(key)
        _CATEGORY_LOADING.discard(key)
        if source_name != self.source_combo.currentText() or project_type != self.project_type:
            return
        self._categories[key] = []
        self._populate_categories()
        text = str(message or '').strip() or '请稍后重试'
        show_toast(f'分类加载失败：{text}', 'error', self, 4500)

    def _populate_categories(self):
        source_name = self.source_combo.currentText()
        categories = self._categories.get((source_name, self.project_type), [])
        current = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem('全部分类', '')
        for cid, name in categories:
            self.category_combo.addItem(name, cid)
        idx = self.category_combo.findData(current) if current else -1
        if idx >= 0:
            self.category_combo.setCurrentIndex(idx)
        self.category_combo.blockSignals(False)

    def _on_filter_changed(self):
        self._trigger_search()

    def _trigger_search(self):
        self._search_timer.start(300)

    def reset_filters(self):
        self.search_input.clear()
        self.source_combo.setCurrentIndex(0)
        self.mc_combo.setCurrentIndex(-1)
        self.mc_combo.setEditText('')
        self.loader_combo.setCurrentIndex(0)
        self.category_combo.setCurrentIndex(0)
        self.sort_combo.setCurrentIndex(0)
        self._page = 1
        self.search()

    def search(self):
        self._search_timer.stop()
        self._load_categories()
        source_name = self.source_combo.currentText()
        query = self.search_input.text().strip()
        mc_version = self._mc_filter_value()
        if mc_version in ('全部', '全部版本', '自定义...'):
            mc_version = ''
        loader = self.loader_combo.currentData() or ''
        category = self.category_combo.currentData() or ''
        sort = self.sort_combo.currentData() or 'relevance'
        self._generation += 1
        gen = self._generation
        old_worker = self._search_worker
        if old_worker is not None and old_worker.isRunning():
            try:
                old_worker.cancel()
            except RuntimeError:
                pass
        self._show_loading('正在加载...')
        offset = (self._page - 1) * self.PAGE_SIZE
        worker = SearchWorker(gen, source_name, query, self.project_type, mc_version, loader, category, sort, offset, self.PAGE_SIZE)
        worker.done.connect(self._on_search_done)
        worker.error.connect(self._on_search_error)
        self._search_worker = worker
        self._search_timeout.start()
        self._workers.append(worker)
        worker.finished.connect((lambda w=worker: self._release_worker(w)))
        _track_worker(worker)

    def _retry_search(self):
        self._loaded_once = False
        self.status_label.setText('正在重试...')
        self.search()

    def _release_worker(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)
        if self._search_worker is worker:
            self._search_worker = None

    def _on_search_timeout(self):
        '''搜索超过 15 秒仍未返回时结束加载态，避免社区页一直卡在转圈。'''

        worker = self._search_worker
        if worker is not None and worker.isRunning():
            try:
                worker.cancel()
            except RuntimeError:
                pass
        self._generation += 1
        self._loaded_once = False
        self.status_label.setText('加载超时')
        self.loading_overlay.fail('加载超时，请检查网络后重试')

    def _on_search_done(self, generation, result):
        if generation != self._generation:
            return
        self._search_timeout.stop()
        self._total = max(0, result.total)
        pages = max(1, math.ceil(self._total / self.PAGE_SIZE))
        if self._page > pages:
            self._page = pages
            self.search()
            return
        self.pagination.set_pages(self._page, pages)
        if not self.isVisible():
            # 离页时不要为不可见页面创建 20 个卡片；结果留到回来时再渲染。
            self._last_projects = list(result.projects or [])
            self.loading_overlay.stop()
            if not self._last_projects:
                self._loaded_once = False
            return
        self._render_projects(result.projects)

    def _on_search_error(self, generation, message):
        if generation != self._generation:
            return
        self._search_timeout.stop()
        self._total = 0
        self._page = 1
        self.pagination.set_pages(1, 1)
        logger.warning('社区资源加载失败: %s', message)
        if is_network_error_text(message) or not network_available():
            reason = '未连接到互联网，无法获取搜索结果'
        else:
            reason = '加载失败，请点击重试'
        self._render_token += 1
        self.results_list.setUpdatesEnabled(True)
        self._clear_results()
        self._last_projects = []
        self._loaded_once = False
        self.status_label.setText('')
        self.loading_overlay.fail(reason)

    def _show_offline_placeholder(self):
        self._clear_results()
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)
        item.setSizeHint(QSize(100, 120))
        label = QLabel('未连接到互联网')
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet('color:rgba(255,255,255,0.55); font-size:13px; background:transparent;')
        self.results_list.addItem(item)
        self.results_list.setItemWidget(item, label)
        self.status_label.setText('未连接到互联网')
        self.status_label.setStyleSheet('color:rgba(255,255,255,0.5); font-size:12px;')

    def _show_empty_results(self, text):
        self._clear_results()
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)
        item.setSizeHint(QSize(100, 150))
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        label = QLabel(str(text or '暂无内容'))
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet('color: rgba(255,255,255,0.55); font-size: 13px; background: transparent;')
        layout.addWidget(label)
        if self.project_type == 'modpack':
            button = QPushButton('安装已下载整合包')
            button.setStyleSheet(self._secondary_style())
            button.clicked.connect(self._choose_local_pack)
            layout.addWidget(button, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        self.results_list.addItem(item)
        self.results_list.setItemWidget(item, host)

    def _clear_results(self):
        """显式销毁 QListWidgetItem 的 itemWidget，避免切页/刷新时卡片漂在 viewport 上。"""
        for index in range(self.results_list.count() - 1, -1, -1):
            item = self.results_list.item(index)
            if item is None:
                continue
            widget = self.results_list.itemWidget(item)
            if widget is not None:
                self.results_list.removeItemWidget(item)
                widget.setParent(None)
                widget.deleteLater()
            self.results_list.takeItem(index)
        self.results_list.clear()
        self._cards.clear()

    def _render_current(self):
        if not self._cards:
            return
        text = self.search_input.text().strip().lower()
        mode = self.name_match_combo.currentData()
        shown = 0
        for card in self._cards:
            item = getattr(card, '_list_item', None)
            if item is None:
                continue
            title = f'{card.project.title} {card.project.english_title} {card.project.translated_title} {card.project.slug}'.lower()
            if not text:
                item.setHidden(False)
                shown += 1
            elif mode == 'startswith':
                visible = title.startswith(text) or card.project.translated_title.lower().startswith(text)
                item.setHidden(not visible)
                shown += int(visible)
            else:
                visible = text in title or text in card.project.description.lower()
                item.setHidden(not visible)
                shown += int(visible)
        self.status_label.setText(f'共 {self._total} 个结果，本页显示 {shown}/{len(self._cards)} 个')

    def _render_projects(self, projects):
        self._render_token += 1
        token = self._render_token
        self._last_projects = list(projects or [])
        self._clear_results()
        # 数据已经返回，立即撤掉居中加载卡片。列表卡片会分帧插入，
        # 不能等全部插完再隐藏遮罩，否则会出现“列表已经显示，加载卡片
        # 还盖在中间”的情况。
        self.loading_overlay.stop()
        if not projects:
            self._show_empty_results('未找到匹配的内容')
            self.status_label.setText('未找到匹配的内容')
            self.status_label.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
            return
        self._render_project_batch(token, list(projects), 0)

    def _render_project_batch(self, token, projects, start):
        '''分帧创建项目卡片，避免社区页一次性构建 20 个卡片造成可见卡顿。'''

        if token != self._render_token:
            return
        end = min(start + 1, len(projects))
        for project in projects[start:end]:
            card = ProjectCard(project)
            card.clicked.connect(self._download_project)
            card.download_clicked.connect(self._download_project)
            item = QListWidgetItem()
            item.setSizeHint(QSize(100, 108))
            self.results_list.addItem(item)
            self.results_list.setItemWidget(item, card)
            card._list_item = item
            self._cards.append(card)
            pix = self.icon_loader.cached(project.icon_url)
            if pix:
                card.set_icon(pix)
            elif project.icon_url:
                self.icon_loader.request(project.icon_url)
        if end < len(projects):
            QTimer.singleShot(
                0,
                lambda: self._render_project_batch(token, projects, end),
            )
            return
        self.status_label.setText(f'第 {self._page} 页 · 共 {self._total} 个结果 · 来源 {self.source_combo.currentText()}')
        self.status_label.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
        try:
            self.results_list.fade_in()
        except RuntimeError:
            pass

    def _on_icon_ready(self, url, pix):
        for card in self._cards:
            if card.icon_url == url:
                card.set_icon(pix)

    def _on_page_changed(self, page):
        self._page = page
        self.search()

    def _show_project(self, project):
        if project.website_url:
            QDesktopServices.openUrl(QUrl(project.website_url))
            return
        QMessageBox.information(self, bilingual_plain(project.translated_title, project.english_title), f'{project.description}\n\n来源: {project.source}\n下载量: {project.downloads:,}')

    def _ensure_selected_version(self):
        '''未选择版本时先让用户选择，避免下载到全局根目录。'''

        current = str(self.config.get('selected_version', '') or '').strip()
        try:
            rows = self.version_mgr.get_installed_versions()
        except Exception:
            rows = []
        ids = []
        for row in rows:
            vid = str(row.get('id') if isinstance(row, dict) else row or '').strip()
            if vid and vid not in ids:
                ids.append(vid)
        if current and current in ids:
            return True
        if not ids:
            return False
        dialog = QInputDialog(self)
        dialog.setWindowTitle('选择游戏版本')
        dialog.setLabelText('当前没有选择游戏版本，请选择要安装到的版本：')
        dialog.setComboBoxItems(ids)
        dialog.setComboBoxEditable(False)
        dialog.setOkButtonText('选择')
        dialog.setCancelButtonText('取消')
        dialog.setStyleSheet(
            'QDialog { background:#1f2229; color:#f0f0f0; }'
            'QLabel { color:#f0f0f0; }'
            'QComboBox { background:rgba(255,255,255,0.08); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.16); border-radius:6px; padding:4px 8px; }'
            ''
            'QPushButton { background:#3a3d47; color:#f0f0f0; border:none;'
            ' border-radius:6px; padding:6px 14px; }'
        )
        if dialog.exec() != QInputDialog.Accepted:
            return False
        version_id = str(dialog.textValue() or '').strip()
        if not version_id:
            return False
        self.config.set('selected_version', version_id)
        self.config.save()
        return True

    def _target_game_dir(self):
        base = Path(self.config.get_mc_dir())
        vid = str(self.config.get('selected_version', '') or '').strip()
        if vid:
            try:
                return self.launch_mgr.get_game_dir(vid)
            except Exception:
                pass
        return base

    @staticmethod
    def _strip_loader_suffix(value):
        """只去掉实例名尾部的加载器后缀，保留完整快照版本号。"""

        text = str(value or '').strip()
        if not text:
            return ''
        text = re.sub(
            r'(?i)[-_](?:forge|fabric|neoforge|quilt|optifine|liteloader)'
            r'(?:[-_].*)?$',
            '',
            text,
        )
        return text.strip(' -_')

    def _target_mc_version_from_instance(self):
        vid = str(self.config.get('selected_version', '') or '').strip()
        if not vid:
            return ''
        try:
            path = Path(self.config.get_mc_dir()) / 'versions' / vid / f'{vid}.json'
            data = json.loads(path.read_text(encoding='utf-8'))
            for candidate in data.get('clientVersion'), data.get('inheritsFrom'), vid:
                if not candidate:
                    continue
                base = self._strip_loader_suffix(candidate)
                if base and re.search(r'\d', base):
                    return base
            return ''
        except Exception:
            return ''

    def _target_mc_version(self):
        value = self._target_mc_version_from_instance()
        if value:
            return value
        value = self._mc_filter_value()
        if value == '全部':
            value = ''
        if re.match('^\\d+\\.\\d+', value or ''):
            return value
        return ''

    def _mc_filter_value(self):
        text = self.mc_combo.currentText().strip()
        index = self.mc_combo.currentIndex()
        data = self.mc_combo.currentData()
        if index == 0:
            if text and text != self.mc_combo.itemText(0):
                return text
            return ''
        if data and index >= 0 and text == self.mc_combo.itemText(index):
            return str(data)
        if text in ('全部', '全部（也可自行输入）', '全部(也可自行输入)'):
            return ''
        return text

    def _show_loading(self, text='正在加载...'):
        self._render_token += 1
        self.results_list.setUpdatesEnabled(True)
        self._clear_results()
        self.status_label.setText('')
        self.loading_overlay.start_auto('正在加载搜索结果')

    def _formatted_filename(self, project, mod_file):
        try:
            fmt = ConfigManager().get('mod_filename_format', 'bracket_title')
        except Exception:
            fmt = 'bracket_title'
        original = sanitize_filename(mod_file.filename, 'download.jar')
        stem = Path(original).stem
        suffix = Path(original).suffix or '.jar'
        title = sanitize_filename(project.translated_title or project.english_title or stem, stem)
        if fmt == 'bracket_title':
            name = f'[{title}] {stem}{suffix}'
        elif fmt == 'bracket_title_cn':
            name = f'【{title}】 {stem}{suffix}'
        elif fmt == 'bracket_title_compact':
            name = f'[{title}]{stem}{suffix}'
        elif fmt == 'title_filename':
            name = f'{title}-{stem}{suffix}'
        elif fmt == 'filename_title':
            name = f'{stem}-{title}{suffix}'
        else:
            name = original
        return sanitize_filename(name, original)

    def _download_project(self, project):
        if self._files_page is not None:
            self._close_files_page()
        # 整合包自带游戏版本，下载阶段不依赖已安装的游戏；导入任务会
        # 自动安装原版和加载器。只有 Mod/资源包等需要当前实例。
        if self.project_type != 'modpack':
            try:
                installed_count = len(self.version_mgr.get_installed_versions())
            except Exception:
                installed_count = 0
            if installed_count == 0:
                self.status_label.setText('未安装任何游戏版本，请先下载游戏')
                dialog = DarkQuestionDialog(self, '需要先安装游戏', '未安装任何游戏版本，请先下载游戏后再安装 Mod。', actions=[('前往下载游戏', 'go', True), ('取消', 'cancel', False)])
                dialog.exec()
                if dialog.choice == 'go':
                    self.download_game_requested.emit()
                return
            if not self._ensure_selected_version():
                return
        from core.net import DownloadGate
        if DownloadGate.available() <= 0:
            QMessageBox.warning(self, '下载任务已满', f'当前同时下载任务已达到上限 {DownloadGate.max_threads()}。\n请前往「全局设置 → 下载线程数」调整后再试。')
            return
        mc_version = self._target_mc_version()
        loader = self.loader_combo.currentData() or ''
        game_dir = self._target_game_dir()
        page = ProjectFilesDialog(
            project, mc_version, loader, game_dir, self.project_type, self,
            embedded=True,
            preferred_source=self.source_combo.currentText(),
        )
        page.dependency_selected.connect(self._download_project)
        page.accepted.connect((lambda p=page, pr=project: self._on_files_page_accepted(p, pr)))
        page.rejected.connect(self._close_files_page)
        self._files_page = page
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    def _on_files_page_accepted(self, page, project):
        mod_file = page.selected_file()
        world = page.selected_world()
        game_dir = page.game_dir
        manual_dir = page.install_dir()
        self._close_files_page()
        if not mod_file or not mod_file.download_url:
            QMessageBox.warning(self, '无法下载', '没有选择文件或文件没有下载地址')
            return
        self._start_project_download(project, mod_file, world, game_dir, manual_dir=manual_dir)

    def _close_files_page(self):
        page = self._files_page
        self._files_page = None
        if page is not None:
            try:
                page.cleanup()
            except Exception:
                pass
            self.stack.removeWidget(page)
            page.deleteLater()
        self.stack.setCurrentWidget(self._browser_widget)

    def _download_status_style(self, level='normal'):
        light = False
        try:
            light = QApplication.palette().color(QPalette.Window).lightness() > 128
        except Exception:
            pass
        colors = {
            'error': '#B42318' if light else '#FF8A8A',
            'warning': '#8A5A00' if light else '#F0B45A',
            'success': '#2F6F6F' if light else '#A8E6E6',
            'normal': 'rgba(0,0,0,0.55)' if light else 'rgba(255,255,255,0.55)',
        }
        return f'color:{colors.get(level, colors["normal"])}; font-size:12px;'

    def _set_download_status(self, text, level='normal'):
        self._download_status_text = str(text or '')
        self._download_status_level = str(level or 'normal')
        self.status_label.setText(self._download_status_text)
        self.status_label.setStyleSheet(
            self._download_status_style(self._download_status_level)
        )

    def _apply_download_status_style(self):
        if (
            getattr(self, '_download_status_text', '')
            and self.status_label.text() == self._download_status_text
        ):
            self.status_label.setStyleSheet(
                self._download_status_style(self._download_status_level)
            )

    def set_light(self, light):
        '''主题切换时刷新下载状态文字，避免沿用旧主题颜色。'''

        self._apply_download_status_style()

    def _refresh_download_bar_visibility(self):
        active = [
            worker for worker, ctx in self._download_contexts.items()
            if not ctx.get('terminal')
        ]
        if not active:
            self._download_current = None
            self.download_bar.hide()
            self.download_bar.setRange(0, 100)
            return
        if self._download_current not in active:
            self._download_current = active[-1]
        self.download_bar.setRange(0, 100)
        self.download_bar.show()

    def _start_project_download(self, project, mod_file, world, game_dir, manual_dir=None):
        display_name = project.translated_title or project.english_title or project.title
        kind = 'modpack' if self.project_type == 'modpack' else 'file'
        if kind == 'modpack':
            pack_dir = PROJECT_TEMP / 'modpacks'
            pack_dir.mkdir(parents=True, exist_ok=True)
            dest = unique_path(
                pack_dir,
                sanitize_filename(mod_file.filename, 'modpack.mrpack'),
            )
        else:
            if self.project_type == 'datapack':
                # 新流程的 world 已经是目标文件夹（saves/<世界>/datapacks
                # 或游戏目录/自定义目录）；兼容旧调用传入的世界名。
                target = (
                    Path(manual_dir) if manual_dir
                    else Path(world) if world else Path(game_dir)
                )
                if not target.is_absolute():
                    target = game_dir / 'saves' / target / 'datapacks'
                dest_dir = target
            elif self.project_type == 'resourcepack':
                dest_dir = game_dir / 'resourcepacks'
            elif self.project_type == 'shader':
                dest_dir = game_dir / 'shaderpacks'
            else:
                dest_dir = game_dir / 'mods'
            if manual_dir is not None:
                dest_dir = Path(manual_dir)
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if not dest_dir.is_dir():
                    raise OSError('not a directory')
            except OSError:
                folder_name = dest_dir.name or 'mods'
                QTimer.singleShot(
                    120,
                    (lambda: self._choose_manual_folder(project, mod_file, world, game_dir, folder_name)),
                )
                return
            dest = unique_path(dest_dir, self._formatted_filename(project, mod_file))
        worker = DownloadWorker(
            mod_file.download_url, dest, mod_file.sha1, mod_file.size,
            sha512=getattr(mod_file, 'sha512', ''),
        )
        task_id = f'community-{self.project_type}-{id(self)}-{time.monotonic_ns()}'
        context = {
            'task_id': task_id,
            'kind': kind,
            'dest': str(dest),
            'name': project.title,
            'icon': project.icon_url or '',
            'display': display_name,
            'filename': mod_file.filename,
            'size': int(mod_file.size or 0),
            'percent': 0.0,
            'terminal': False,
            'last_time': time.monotonic(),
            'last_done': 0,
            'speed_text': '',
        }
        self._download_contexts[worker] = context
        self._download_tasks[task_id] = worker
        self._download_current = worker
        manager = DownloadManager.instance()
        manager.start_task(
            task_id,
            f'下载 {display_name}',
            steps=[{
                'text': f'下载 {mod_file.filename}',
                'state': 'active',
                'percent': 0,
            }],
            cancel_callback=worker.cancel,
        )
        manager.update_task(task_id, percent=0.0, status='准备下载...')
        manager.update_step(task_id, 0, state='active', percent=0)
        worker.status.connect(
            (lambda text, w=worker: self._on_download_status(w, text))
        )
        worker.progress.connect(
            (lambda done, total, w=worker: self._on_download_progress(w, done, total))
        )
        worker.done.connect(
            (lambda path, w=worker: self._on_download_done(w, path))
        )
        worker.error.connect(
            (lambda message, w=worker: self._on_download_error(w, message))
        )
        worker.finished.connect(
            (lambda w=worker: self._on_download_finished(w))
        )
        self._workers.append(worker)
        worker.finished.connect((lambda w=worker: self._release_worker(w)))
        self._set_download_status(f'准备下载 {display_name}...', 'normal')
        self.download_bar.reset_value()
        self._refresh_download_bar_visibility()
        _track_worker(worker)

    def _choose_manual_folder(self, project, mod_file, world, game_dir, folder_name='mods'):
        QMessageBox.warning(
            self,
            '未找到安装文件夹',
            f'当前没有合适的 {folder_name} 文件夹，请手动选择位置。',
        )
        chosen = QFileDialog.getExistingDirectory(self, '选择安装位置', str(game_dir))
        if chosen:
            self._start_project_download(project, mod_file, world, game_dir, Path(chosen))

    def _on_download_status(self, worker, text):
        context = self._download_contexts.get(worker)
        if (
            context is None
            or context.get('terminal')
            or getattr(worker, '_cancelled', False)
        ):
            return
        text = str(text or '')
        DownloadManager.instance().update_task(context['task_id'], status=text)
        if worker is self._download_current:
            self._set_download_status(text, 'normal')

    def _on_download_progress(self, worker, done, total):
        context = self._download_contexts.get(worker)
        if (
            context is None
            or context.get('terminal')
            or getattr(worker, '_cancelled', False)
        ):
            return
        done = max(0, int(done or 0))
        total = max(0, int(total or 0))
        if total <= 0:
            total = int(context.get('size') or 0)
        percent = min(100.0, done * 100.0 / total) if total > 0 else 0.0
        now = time.monotonic()
        elapsed = now - float(context.get('last_time') or now)
        if elapsed >= 0.35:
            moved = done - int(context.get('last_done') or 0)
            if moved >= 0:
                speed = moved / elapsed
                context['speed_text'] = f'{speed / 1024 / 1024:.2f} MB/s'
            context['last_time'] = now
            context['last_done'] = done
        context['percent'] = percent
        manager = DownloadManager.instance()
        manager.update_task(
            context['task_id'],
            percent=percent,
            speed=context.get('speed_text') or '0 B/s',
        )
        manager.update_step(
            context['task_id'], 0,
            state='active', percent=percent,
        )
        if worker is not self._download_current:
            return
        self.download_bar.setRange(0, 100)
        self.download_bar.set_animated_value(int(percent))
        if total > 0:
            text = (
                f'下载中... {done / 1024 / 1024:.1f} / '
                f'{total / 1024 / 1024:.1f} MB · {percent:.2f}%'
            )
        else:
            text = f'下载中... {done / 1024 / 1024:.1f} MB · {percent:.2f}%'
        speed_text = context.get('speed_text') or ''
        if speed_text:
            text += f' · {speed_text}'
        self._set_download_status(text, 'normal')

    def _on_download_done(self, worker, path):
        context = self._download_contexts.get(worker)
        if context is None:
            return
        if getattr(worker, '_cancelled', False):
            context['terminal'] = True
            DownloadManager.instance().finish_task(
                context['task_id'], False, '已取消'
            )
            return
        context['terminal'] = True
        path = str(path)
        manager = DownloadManager.instance()
        manager.finish_task(context['task_id'], True, '下载完成')
        if worker is self._download_current:
            if context['kind'] == 'modpack':
                self._set_download_status('整合包下载完成，准备导入...', 'success')
            else:
                self._set_download_status(f'下载完成: {path}', 'success')
        self._refresh_download_bar_visibility()
        if context['kind'] == 'modpack':
            self.import_pack_requested.emit(
                path, '', context.get('icon') or ''
            )
            return
        display = context.get('display') or Path(path).stem
        QTimer.singleShot(
            150,
            (lambda n=display: show_toast(f'{n} 下载成功！', 'success', self, 3500)),
        )

    def _on_download_error(self, worker, message):
        context = self._download_contexts.get(worker)
        if context is None:
            return
        context['terminal'] = True
        message = str(message or '下载失败')
        network_problem = (
            not is_network_error_text(message)
            and not network_available()
            and '校验' not in message
        )
        shown_message = '未连接到互联网' if network_problem else message
        DownloadManager.instance().finish_task(
            context['task_id'], False, shown_message
        )
        if worker is self._download_current:
            self._set_download_status(
                shown_message,
                'warning' if network_problem else 'error',
            )
        self._refresh_download_bar_visibility()
        if network_problem:
            notify_network_failure(self, '下载文件')
            return
        QMessageBox.warning(self, '下载失败', message)

    def _on_download_finished(self, worker):
        context = self._download_contexts.pop(worker, None)
        if context is None:
            self._release_worker(worker)
            return
        task_id = context['task_id']
        self._download_tasks.pop(task_id, None)
        cancelled = bool(getattr(worker, '_cancelled', False))
        if not context.get('terminal'):
            message = '已取消' if cancelled else '下载已停止'
            DownloadManager.instance().finish_task(task_id, False, message)
        elif cancelled:
            # 取消任务时 cancel_task 已经移除了卡片。
            DownloadManager.instance().finish_task(task_id, False, '已取消')
        if cancelled and worker is self._download_current:
            self._set_download_status('已取消下载', 'warning')
        if worker is self._download_current:
            self._download_current = None
        self._refresh_download_bar_visibility()
        self._release_worker(worker)

    def _choose_local_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择整合包', '', 'Minecraft 整合包 (*.mrpack *.zip);;所有文件 (*.*)')
        if path:
            self.import_pack_requested.emit(path, '', '')

    def has_active_downloads(self):
        for worker in self._workers:
            try:
                if isinstance(worker, DownloadWorker) and worker.isRunning() and not getattr(worker, '_cancelled', False):
                    return True
            except RuntimeError:
                continue
        return False

    def hideEvent(self, event):
        self.icon_loader.set_enabled(False)
        self._cancel_loading_workers()
        self._render_token += 1
        self._clear_results()
        super().hideEvent(event)

    def _cancel_loading_workers(self):
        """离页时只取消可中断的辅助请求，搜索线程让它自然收尾。

        QThread.terminate() 会绕过 Python 清理和 finished 信号，线程对象
        以及其请求上下文会一直留在内存中；快速切页时会出现线性增长。
        搜索请求最多一个在跑，让它按 HTTP 超时结束即可。
        """

        workers = [
            worker for worker in list(self._workers)
            if not isinstance(worker, DownloadWorker) and worker is not self._search_worker
        ]
        for worker in workers:
            cancel = getattr(worker, 'cancel', None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass

    def shutdown(self):
        self._render_token += 1
        try:
            self.results_list.setUpdatesEnabled(True)
        except Exception:
            pass
        try:
            self._search_timeout.stop()
        except Exception:
            pass
        try:
            self.loading_overlay.stop()
        except Exception:
            pass
        if self._files_page is not None:
            self._close_files_page()
        self.icon_loader.stop()
        manager = DownloadManager.instance()
        for context in list(self._download_contexts.values()):
            if not context.get('terminal'):
                manager.cancel_task(context.get('task_id'))
        for worker in list(self._workers):
            cancel = getattr(worker, 'cancel', None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass
        for worker in list(self._workers):
            try:
                if not worker.wait(400):
                    worker.terminate()
                    worker.wait(100)
            except RuntimeError:
                continue
        self._workers.clear()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


# WARNING: Decompyle incomplete
