'''
社区资源下载页（通用）
支持 Modrinth / CurseForge 的 模组、整合包、数据包、资源包、光影包 浏览与下载。
特性：分页页码、完善筛选、异步图标加载（LRU 缓存）、线程安全清理。
'''

from __future__ import annotations
import json
import math
import re
import time
import weakref
from collections import OrderedDict, deque
from pathlib import Path
from typing import List, Optional
from PySide6.QtCore import (Qt, Signal, QThread, QTimer, QUrl, QSize, QObject, QEvent)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPixmap)
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkDiskCache, QNetworkReply, QNetworkRequest)
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QAbstractItemView, QProgressBar, QSizePolicy, QRadioButton, QButtonGroup, QScrollArea, QToolButton, QDialogButtonBox, QStackedWidget)
from core.app_font import ui_font
from ui.widgets.back_button import BackButton
from ui.widgets.dark_dialog import DarkQuestionDialog
from ui.widgets.loading_overlay import LoadingOverlay
from ui.widgets.smooth_progress import SmoothProgressBar
from ui.widgets.painted_icons import painted_icon, set_button_icon
from core.config import ConfigManager
from core.launch import LaunchManager
from core.mods import (LOADER_RELEVANT_TYPES, LOADERS, PROJECT_TYPES, SORTS, ModFile, ModProject, SearchResult, SourceUnavailable, _natural_key, cached_mc_versions as fetch_cached_mc_versions, download_candidates, fetch_mc_versions, get_source)
from core.mod_translations import bilingual_html, bilingual_plain, translation_db
from core.net import (DownloadError, HttpClient, sanitize_filename, unique_path, is_network_error_text, network_available)
from core.paths import scl_dir
from ui.widgets.toast import notify_network_failure
from core.version import VersionManager
PROJECT_TEMP = scl_dir() / 'temp_install'
PROJECT_TEMP.mkdir(parents=True, exist_ok=True)
_ACTIVE_WORKERS: 'set' = set()

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

class VersionComboBox(QComboBox):
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
        self._update_arrow()
        self.lineEdit().installEventFilter(self)
        self.view().setProperty('_theme_ignore', True)
        self._apply_view_style()

    def _apply_view_style(self):
        if self._light:
            style = (
                'QAbstractItemView { background:#fbfbfb; color:#1b1b1b;'
                ' selection-background-color:#0078d4; selection-color:white;'
                ' border:1px solid #c8ccd2; }'
            )
        else:
            style = (
                'QAbstractItemView { background:#20232b; color:#f0f0f0;'
                ' selection-background-color:#0078d4; selection-color:white;'
                ' border:1px solid rgba(255,255,255,0.18); }'
            )
        self.view().setStyleSheet(style)

    def set_light(self, light):
        self._light = bool(light)
        self._apply_view_style()
        self._update_arrow()

    def _update_arrow(self):
        set_button_icon(
            self._arrow_btn,
            'chevron_up' if self._arrow_open else 'chevron_down',
            14,
            '#cfd6e4',
        )

    def _toggle_popup(self):
        if self.view().isVisible():
            self.hidePopup()
        else:
            self.showPopup()

    def showPopup(self):
        self._apply_view_style()
        super().showPopup()
        self._arrow_open = True
        self._update_arrow()

    def hidePopup(self):
        super().hidePopup()
        self._arrow_open = False
        self._update_arrow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrow_btn.move(
            max(0, self.width() - 25),
            max(0, (self.height() - self._arrow_btn.height()) // 2),
        )
        self._arrow_btn.raise_()

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() == QEvent.MouseButtonPress:
            if self.view().isVisible():
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
            source = get_source(self.source_name)
            result = source.search(self.query, self.project_type, mc_version=self.mc_version, loader=self.loader, category=self.category, sort=self.sort, offset=self.offset, limit=self.limit)
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
    def __init__(self, url, dest, sha1='', size=0, urls=None):
        super().__init__()
        self.url = url
        self.urls = list(urls) if urls else download_candidates(url)
        self.dest = Path(dest)
        self.sha1 = sha1
        self.size = size
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        http = HttpClient(timeout=15, retries=1, verify=False)
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
                    http.download(url, self.dest, sha1=self.sha1 or None, size=self.size or None, progress=progress, cancel=(lambda: self._cancelled))
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
    def __init__(self, source_name, project_type):
        super().__init__()
        self.source_name = source_name
        self.project_type = project_type

    def run(self):
        try:
            source = get_source(self.source_name)
            categories = source.get_categories(self.project_type)
            self.done.emit(self.source_name, self.project_type, categories)
        except Exception:
            self.done.emit(self.source_name, self.project_type, [])


class IconLoader(QObject):
    ready = Signal(str, QPixmap)
    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
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
        self._max_active = 12
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
                req.setTransferTimeout(8000)
            except Exception:
                pass
            reply = self._manager.get(req)
            self._replies[reply] = url
            reply.finished.connect((lambda r=reply: self._on_finished(r)))
            if self._enabled and self._queue:
                pass

    def _on_finished(self, reply):
        url = self._replies.pop(reply, None)
        if url:
            self._active.discard(url)
        try:
            if reply.error() == QNetworkReply.NoError:
                data = reply.readAll()
                pix = QPixmap()
                if pix.loadFromData(data):
                    if not pix.isNull():
                        scaled = pix.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        self._cache[url] = scaled
                        while len(self._cache) > self._cache_limit:
                            self._cache.popitem(last=False)
                        self.ready.emit(url, scaled)
        except RuntimeError:
            pass
        reply.deleteLater()
        self._process()

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


class ProjectCard(QFrame):
    clicked = Signal(object)
    download_clicked = Signal(object)
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.icon_url = project.icon_url
        self.setObjectName('ProjectCard')
        self.setStyleSheet('\n            QFrame#ProjectCard {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.18);\n                border-radius: 10px;\n            }\n            QFrame#ProjectCard:hover {\n                background: rgba(28,30,38,0.72);\n                border: 1px solid rgba(122,160,255,0.45);\n            }\n        ')
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(92)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(64, 64)
        self.icon_label.setStyleSheet('background: rgba(255,255,255,0.06); border-radius: 10px; color: #888;')
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setText('?')
        layout.addWidget(self.icon_label)
        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        title = QLabel()
        title.setTextFormat(Qt.RichText)
        title.setText(bilingual_html(project.translated_title, project.title, 12, 10))
        title.setStyleSheet('background: transparent;')
        text_box.addWidget(title)
        desc = QLabel(project.description or '暂无描述')
        desc.setWordWrap(True)
        desc.setMaximumHeight(38)
        desc.setStyleSheet('color: rgba(255,255,255,0.62); font-size: 12px; background: transparent;')
        text_box.addWidget(desc)
        cats = ' / '.join(project.categories[:4]) if project.categories else PROJECT_TYPES.get(project.project_type, '')
        meta = f'{project.source}  ·  下载 {project.downloads:,}  ·  更新 {project.updated}'
        if cats:
            meta += f'  ·  {cats}'
        meta_label = QLabel(meta)
        meta_label.setStyleSheet('color: rgba(255,255,255,0.42); font-size: 11px; background: transparent;')
        text_box.addWidget(meta_label)
        layout.addLayout(text_box, 1)
        actions = QVBoxLayout()
        actions.setSpacing(6)
        dl_btn = QPushButton('下载')
        dl_btn.setFixedWidth(76)
        dl_btn.setStyleSheet('\n            QPushButton {\n                background: #0078d4; color: white; border: none;\n                border-radius: 6px; padding: 6px 10px;\n            }\n            QPushButton:hover { background: #106ebe; }\n        ')
        dl_btn.clicked.connect((lambda: self.download_clicked.emit(self.project)))
        actions.addWidget(dl_btn)
        open_btn = QPushButton('主页')
        open_btn.setFixedWidth(76)
        open_btn.setStyleSheet('\n            QPushButton {\n                background: #3a3a3a; color: white; border: none;\n                border-radius: 6px; padding: 6px 10px;\n            }\n            QPushButton:hover { background: #4a4a4a; }\n        ')
        open_btn.clicked.connect(self._open_homepage)
        actions.addWidget(open_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

    def set_icon(self, pix):
        if pix is None or pix.isNull():
            return
        from ui.widgets.settings_widgets import hidpi_pixmap, rounded_pixmap
        size = self.icon_label.width() or 64
        pixmap = hidpi_pixmap(pix, size, size)
        self.icon_label.setPixmap(rounded_pixmap(pixmap, max(6, round(size * 0.22))))
        self.icon_label.setText('')

    def _open_homepage(self):
        if self.project.website_url:
            QDesktopServices.openUrl(QUrl(self.project.website_url))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            child = self.childAt(event.position().toPoint())
            if child is None or not isinstance(child, QPushButton):
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
            btn.setStyleSheet('\n                QPushButton {\n                    background: rgba(255,255,255,0.06);\n                    color: #ddd; border: none; border-radius: 5px;\n                }\n                QPushButton:hover { background: rgba(255,255,255,0.14); }\n                QPushButton:checked { background: #0078d4; color: white; }\n            ')
            btn.clicked.connect((lambda checked=False, p=page: self._goto(p)))
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


class CollapsibleSection(QWidget):
    def __init__(self, title, subtitle='', highlighted=False, expanded=False, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        text = title + (f'    {subtitle}' if subtitle else '')
        self._toggle.setText(text)
        self._toggle.setCursor(Qt.PointingHandCursor)
        if highlighted:
            self._toggle.setStyleSheet('\n                QToolButton {\n                    text-align: left; padding: 8px 10px; border: none;\n                    border-left: 3px solid #0078d4; border-radius: 6px;\n                    background: rgba(0,120,212,0.18); color: #8ec5ff;\n                    font-weight: 600; font-size: 13px;\n                }\n                QToolButton:hover { background: rgba(0,120,212,0.28); }\n            ')
        else:
            self._toggle.setStyleSheet('\n                QToolButton {\n                    text-align: left; padding: 8px 10px; border: none;\n                    border-radius: 6px; background: rgba(255,255,255,0.045);\n                    color: rgba(255,255,255,0.85); font-size: 13px;\n                }\n                QToolButton:hover { background: rgba(255,255,255,0.09); }\n            ')
        outer.addWidget(self._toggle)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(18, 4, 4, 8)
        self._content_layout.setSpacing(4)
        outer.addWidget(self._content)
        self._toggle.toggled.connect(self._on_toggled)
        self._on_toggled(expanded)

    def _on_toggled(self, checked):
        self._toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self._content.setVisible(checked)

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)

    def set_expanded(self, expanded):
        self._toggle.setChecked(expanded)


class FileRow(QWidget):
    clicked = Signal(object)
    def __init__(self, mod_file, current_mc='', project_title='', project_translated='', parent=None):
        super().__init__(parent)
        self.mod_file = mod_file
        self._selected = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
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
        if main_title is not None:
            self.title.setText(f'<span style="font-size:13px;color:#f0f0f0;">{main_title}</span>')
        else:
            self.title.setText(bilingual_html(project_translated, project_title or mod_file.display_name, 13, 11))
        box.addWidget(self.title)
        versions = ', '.join(mod_file.game_versions[:6])
        meta = f'{mod_file.display_name or detail_title}  ·  {detail_title}  ·  {mod_file.release_type}  ·  {versions or "版本未知"}'
        if mod_file.date:
            meta += f'  ·  {mod_file.date}'
        if mod_file.size:
            meta += f'  ·  {mod_file.size / 1024 / 1024:.1f} MB'
        self.meta_label = QLabel(meta)
        self.meta_label.setStyleSheet('color: rgba(255,255,255,0.45); font-size: 11px; background: transparent;')
        self.meta_label.setWordWrap(True)
        box.addWidget(self.meta_label)
        layout.addLayout(box, 1)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName('FileRow')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._apply_style()

    def _apply_style(self):
        if self._selected:
            self.setStyleSheet('\n                QWidget#FileRow { background: rgba(0,120,212,0.30);\n                          border: 1px solid #3399ff; border-radius: 6px; }\n            ')
            return
        self.setStyleSheet('\n                QWidget#FileRow { background: rgba(18,20,26,0.5);\n                          border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; }\n                QWidget#FileRow:hover { background: rgba(28,30,38,0.72);\n                                border: 1px solid rgba(122,160,255,0.4); }\n            ')

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._apply_style()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self)
        super().mouseReleaseEvent(event)


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
        self.icon.setStyleSheet('background:transparent; border:none; color:#888;')
        layout.addWidget(self.icon)
        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel()
        title.setTextFormat(Qt.RichText)
        title.setText(bilingual_html(project.translated_title, project.title, 13, 11))
        title.setStyleSheet('background:transparent;')
        text.addWidget(title)
        sub = QLabel(project.source)
        sub.setStyleSheet('color:rgba(255,255,255,0.45); font-size:11px; background:transparent;')
        text.addWidget(sub)
        layout.addLayout(text, 1)

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
    def __init__(self, project, mc_version, loader, game_dir, project_type, parent=None, embedded=False):
        super().__init__(parent)
        self.project = project
        self.mc_version = mc_version
        self.loader = loader
        self.game_dir = Path(game_dir)
        self.project_type = project_type
        self.embedded = embedded
        self._cleaned = False
        self._worker = None
        self._files = []
        self._file_rows = []
        self._selected_row = None
        self._dep_generation = 0
        self._dep_worker = None
        self._dep_workers = set()
        self._dep_cache = {}
        self._dep_required_order = []
        self._dep_cached_items = {}
        self._dep_icon_loader = IconLoader(self)
        self._dep_icon_loader.ready.connect(self._on_dep_icon_ready)
        self._dep_cards = []
        if embedded:
            self.setWindowFlags(Qt.Widget)
            self.setObjectName('ProjectFilesPage')
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.setStyleSheet('QDialog#ProjectFilesPage { background: transparent; color: #f0f0f0; }')
        else:
            self.setWindowTitle(f'选择版本 - {bilingual_plain(project.translated_title, project.title)}')
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
        title.setText(bilingual_html(project.translated_title, project.title, 16, 12))
        title_row.addWidget(title)
        title_row.addStretch(1)
        layout.addLayout(title_row)
        sub = QLabel(f'{project.source} · {project.description}')
        sub.setWordWrap(True)
        sub.setStyleSheet('color: rgba(255,255,255,0.6);')
        layout.addWidget(sub)
        self.warning_label = QLabel('')
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet('\n            QLabel { background: rgba(255,180,80,0.18); color: #ffcc80;\n                     border-left: 3px solid #f0a030; padding: 7px 10px;\n                     font-size: 12px; }\n        ')
        self.warning_label.hide()
        layout.addWidget(self.warning_label)
        self.dep_panel = QFrame()
        self.dep_panel.setObjectName('DependencyPanel')
        self.dep_panel.setAttribute(Qt.WA_StyledBackground, True)
        self.dep_panel.setStyleSheet('\n            QFrame#DependencyPanel { background: rgba(18,20,26,0.58);\n                     border: 1px solid rgba(255,255,255,0.22);\n                     border-radius: 8px; }\n            QFrame#DependencyPanel QLabel { background: transparent; border: none; }\n        ')
        dep_layout = QVBoxLayout(self.dep_panel)
        dep_layout.setContentsMargins(10, 8, 10, 8)
        dep_layout.setSpacing(6)
        self.dep_title = QLabel('此模组的前置模组')
        self.dep_title.setStyleSheet('color:rgba(255,255,255,0.78); font-size:13px; font-weight:600;')
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
        if project_type == 'datapack':
            world_row = QHBoxLayout()
            world_row.addWidget(QLabel('存档'))
            self.world_combo = QComboBox()
            self.world_combo.setStyleSheet(self._combo_style())
            saves_dir = self.game_dir / 'saves'
            if saves_dir.exists():
                for item in sorted(saves_dir.iterdir(), key=(lambda p: p.name.lower())):
                    if item.is_dir():
                        self.world_combo.addItem(item.name, item.name)
            if self.world_combo.count() == 0:
                self.world_combo.addItem('（未找到存档，请先创建世界）', '')
                self.world_combo.setEnabled(False)
            world_row.addWidget(self.world_combo, 1)
            layout.addLayout(world_row)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.download_btn = QPushButton('下一步')
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet('\n            QPushButton { background: #0078d4; color: white; border: none;\n                          border-radius: 6px; padding: 8px 22px; }\n            QPushButton:hover { background: #106ebe; }\n            QPushButton:disabled { background: #333; color: #777; }\n        ')
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
        self.status_label.setText('')
        self._load_timeout = QTimer(self)
        self._load_timeout.setSingleShot(True)
        self._load_timeout.setInterval(45000)
        self._load_timeout.timeout.connect(self._on_load_timeout)
        self._load_timeout.start()
        self.loading_overlay.start_auto('正在加载文件列表')
        self._worker = FilesWorker(project.source, project, '', loader)
        self._worker.done.connect(self._on_files)
        self._worker.error.connect(self._on_error)
        _track_worker(self._worker)

    def _combo_style(self):
        return '\n            QComboBox {\n                background: rgba(255,255,255,0.06);\n                border: 1px solid rgba(255,255,255,0.12);\n                border-radius: 6px; color: #f0f0f0; padding: 4px 8px;\n            }\n            QComboBox::drop-down { border: none; }\n            QComboBox QAbstractItemView { background: #2a2a2a; color: #f0f0f0; }\n        '

    @staticmethod
    def _release_order(kind):
        return {'release': 0, 'beta': 1, 'alpha': 2}.get((kind or '').lower(), 3)

    @staticmethod
    def _mc_versions(file):
        return [v for v in file.game_versions if re.match('^\\d+\\.\\d+', str(v))]

    def _is_compatible(self, file):
        if not self.mc_version:
            return True
        for version in self._mc_versions(file):
            if version == self.mc_version or self.mc_version.startswith(version + '.'):
                return True
        return False

    def _on_files(self, files):
        try:
            self._load_timeout.stop()
        except (AttributeError, RuntimeError):
            pass
        self.loading_overlay.stop()
        self._files = files or []
        self.retry_btn.hide()
        while self.groups_layout.count():
            item = self.groups_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._file_rows.clear()
        self._selected_row = None
        if not files:
            message = '此 Mod 与当前游戏版本不适配或没有可用文件。'
            if self.mc_version:
                message = f'此 Mod 与当前游戏版本 {self.mc_version} 不适配，未找到可用文件。'
            self.warning_label.setText(message)
            self.warning_label.show()
            self.status_label.setText('没有可用文件')
            self.status_label.setStyleSheet('color: #ff7777; font-size: 12px;')
            self.download_btn.setEnabled(False)
            return
        try:
            groups = {}
            for file in files:
                versions = self._mc_versions(file)
                if self.mc_version:
                    if any((self.mc_version.startswith(v + '.') for v in versions if v != self.mc_version)):
                        key = self.mc_version
                    elif versions:
                        key = sorted(versions, key=_natural_key)[-1]
                    else:
                        key = '未知版本'
                else:
                    key = sorted(versions, key=_natural_key)[-1] if versions else '未知版本'
                groups.setdefault(key, []).append(file)
        except Exception as exc:
            self.warning_label.setText(f'文件列表解析失败：{exc}')
            self.warning_label.show()
            self.status_label.setText('加载失败')
            self.status_label.setStyleSheet('color: #ff7777; font-size: 12px;')
            self.retry_btn.show()
            self.download_btn.setEnabled(False)
            return
        compatible = self.mc_version in groups
        if self.mc_version and not compatible:
            self.warning_label.setText(f'此 Mod 与当前游戏版本 {self.mc_version} 不适配。\n你仍然可以在下方选择其他游戏版本的 Mod 文件手动安装。')
            self.warning_label.show()
        else:
            self.warning_label.hide()
        keys = list(groups.keys())
        keys.sort(key=_natural_key, reverse=True)
        if compatible and self.mc_version in keys:
            keys.remove(self.mc_version)
            keys.insert(0, self.mc_version)
        first_row = None
        for key in keys:
            group_files = groups[key]
            group_files.sort(key=(lambda f: f.date or ''), reverse=True)
            group_files.sort(key=(lambda f: self._release_order(f.release_type)))
            highlighted = bool(self.mc_version and key == self.mc_version)
            section = CollapsibleSection(f'Minecraft {key}', f'{len(group_files)} 个文件' + (' · 当前游戏版本' if highlighted else ''), highlighted=highlighted, expanded=highlighted or key == keys[0] and (not self.mc_version or not compatible))
            for mod_file in group_files:
                row = FileRow(mod_file, self.mc_version, self.project.title, self.project.translated_title)
                row.clicked.connect(self._select_row)
                section.add_widget(row)
                self._file_rows.append(row)
                if first_row is None:
                    first_row = row
            self.groups_layout.addWidget(section)
        self.groups_layout.addStretch(1)
        if first_row is not None:
            self._select_row(first_row)
        self.download_btn.setEnabled(True)
        if self.mc_version:
            self.status_label.setText(f'共 {len(files)} 个文件；当前游戏版本 {self.mc_version}' + ('，已找到适配文件' if compatible else '，未找到精确适配文件'))
        else:
            self.status_label.setText(f'共 {len(files)} 个文件，请选择要安装的版本')
        self.status_label.setStyleSheet('color: rgba(255,255,255,0.55); font-size: 12px;')

    def _on_error(self, message):
        text = str(message)
        network_problem = is_network_error_text(text) or not network_available()
        if network_problem:
            reason = '未连接到互联网，无法获取文件列表'
        elif 'getaddrinfo failed' in text or 'NameResolutionError' in text:
            reason = '网络解析失败（DNS），请检查网络后重试'
        else:
            reason = text or '获取文件列表失败'
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
        self._worker = FilesWorker(self.project.source, self.project, '', self.loader)
        self._worker.done.connect(self._on_files)
        self._worker.error.connect(self._on_error)
        _track_worker(self._worker)

    def _show_dependencies(self, mod_file):
        while self.dep_list_layout.count():
            item = self.dep_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._dep_cards.clear()
        required = []
        seen = set()
        for dep in mod_file.dependencies or []:
            if not isinstance(dep, dict):
                continue
            if (dep.get('dependency_type') or '').lower() != 'required':
                continue
            pid = dep.get('project_id') or dep.get('modId') or dep.get('projectId')
            if pid and str(pid) == str(self.project.project_id):
                continue
            key = _dep_key(dep)
            if not key or key in seen:
                continue
            seen.add(key)
            required.append(dep)
        if not required:
            self.dep_panel.hide()
            return
        self.dep_panel.show()
        self._dep_required_order = [_dep_key(dep) for dep in required]
        cached_items = {key: self._dep_cache[key] for key in self._dep_required_order if key in self._dep_cache}
        missing = [dep for dep in required if _dep_key(dep) not in self._dep_cache]
        if missing:
            self._dep_cached_items = cached_items
            self.dep_title.setText(f'此模组的前置模组（{len(required)}）· 正在加载...')
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
        self.dep_title.setText(f'此模组的前置模组（{len(required)}）')
        self._render_dependency_cards(cached_items)

    def _on_dep_info(self, generation, items):
        if generation != self._dep_generation:
            return
        merged = dict(self._dep_cached_items)
        for key, project, icon in items:
            merged[key] = project, icon
        self._dep_cache.update(merged)
        ordered = [merged[key] for key in self._dep_required_order if key in merged]
        self.dep_title.setText(f'此模组的前置模组（{len(self._dep_required_order)}）')
        self._render_dependency_cards(ordered)

    def _render_dependency_cards(self, items):
        if not items:
            return
        for project, icon_bytes in items:
            card = DependencyCard(project)
            card.clicked.connect(self.dependency_selected.emit)
            self.dep_list_layout.addWidget(card)
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

    def _select_row(self, row):
        if row is None:
            return
        if self._selected_row is not None and self._selected_row is not row:
            self._selected_row.set_selected(False)
        self._selected_row = row
        row.set_selected(True)
        self._show_dependencies(row.mod_file)

    def selected_file(self):
        if self._selected_row is not None:
            return self._selected_row.mod_file

    def selected_world(self):
        if self.world_combo is None:
            return ''
        return self.world_combo.currentData() or ''

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

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)


class ModBrowserPage(QWidget):
    import_pack_requested = Signal(str, str)
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
        self._loaded_once = False
        self._versions_loaded = False
        self._categories = {}
        self._categories_loaded = set()
        self._categories_loading = set()
        self._pending_download = None
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
        top.addWidget(refresh_btn)
        layout.addLayout(top)
        filters = QFrame()
        filters.setStyleSheet('QFrame { background: rgba(18,20,26,0.58); border: 1px solid rgba(255,255,255,0.18); border-radius: 8px; }')
        grid = QGridLayout(filters)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('搜索名称、描述、作者...')
        self.search_input.setMinimumHeight(32)
        self.search_input.setStyleSheet('\n            QLineEdit {\n                background: rgba(255,255,255,0.06);\n                border: 1px solid rgba(255,255,255,0.12);\n                border-radius: 6px; color: #f0f0f0; padding: 4px 8px;\n            }\n        ')
        self.search_input.returnPressed.connect(self._trigger_search)
        grid.addWidget(self.search_input, 0, 0, 1, 3)
        self.source_combo = QComboBox()
        self.source_combo.addItems(['Modrinth', 'CurseForge'])
        self.source_combo.setStyleSheet(self._combo_style())
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        grid.addWidget(self.source_combo, 0, 3)
        self.sort_combo = QComboBox()
        for key, label in SORTS.items():
            self.sort_combo.addItem(label, key)
        self.sort_combo.setStyleSheet(self._combo_style())
        self.sort_combo.currentIndexChanged.connect(self._trigger_search)
        grid.addWidget(self.sort_combo, 0, 4)
        if self.project_type == 'modpack':
            install_btn = QPushButton('安装已有整合包')
            install_btn.setMinimumWidth(120)
            install_btn.setStyleSheet(self._secondary_style())
            install_btn.setToolTip('选择本地的 .mrpack 或 CurseForge 整合包 .zip')
            install_btn.clicked.connect(self._choose_local_pack)
            grid.addWidget(install_btn, 0, 5)
        self.mc_combo = VersionComboBox()
        self.mc_combo.setEditable(True)
        self.mc_combo.setInsertPolicy(QComboBox.NoInsert)
        self.mc_combo.setMinimumWidth(190)
        self.mc_combo.setMaxVisibleItems(20)
        self.mc_combo.setToolTip('默认全部版本，也可以直接输入版本号')
        self.mc_combo.addItem('全部（也可自行输入）', '')
        self.mc_combo.setCurrentIndex(-1)
        self.mc_combo.lineEdit().setPlaceholderText('全部（也可自行输入）')
        self.mc_combo.view().setMinimumWidth(190)
        self.mc_combo.view().setStyleSheet(
            'QAbstractItemView { background: palette(base); color: palette(text);'
            ' selection-background-color:#0078d4; selection-color:white;'
            ' border:1px solid palette(mid); }'
        )
        self.mc_combo.setStyleSheet(
            self._combo_style()
            + 'QComboBox { padding-right: 30px; }'
            + 'QComboBox::drop-down { width: 0px; border: none; }'
            + 'QComboBox::down-arrow { image: none; }'
        )
        self.mc_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.mc_combo.lineEdit().editingFinished.connect(self._trigger_search)
        grid.addWidget(self.mc_combo, 1, 0)
        self.loader_combo = QComboBox()
        self.loader_combo.addItem('全部加载器', '')
        for key, label in LOADERS.items():
            self.loader_combo.addItem(label, key)
        self.loader_combo.setStyleSheet(self._combo_style())
        self.loader_combo.currentIndexChanged.connect(self._trigger_search)
        grid.addWidget(self.loader_combo, 1, 1)
        self.category_combo = QComboBox()
        self.category_combo.addItem('全部分类', '')
        self.category_combo.setStyleSheet(self._combo_style())
        self.category_combo.currentIndexChanged.connect(self._trigger_search)
        grid.addWidget(self.category_combo, 1, 2)
        self.name_match_combo = QComboBox()
        self.name_match_combo.addItem('名称包含', 'contains')
        self.name_match_combo.addItem('名称开头', 'startswith')
        self.name_match_combo.setStyleSheet(self._combo_style())
        self.name_match_combo.setToolTip('仅过滤当前页结果')
        self.name_match_combo.currentIndexChanged.connect(self._render_current)
        grid.addWidget(self.name_match_combo, 1, 3)
        search_btn = QPushButton('搜索')
        search_btn.setStyleSheet(self._primary_style())
        search_btn.clicked.connect(self._trigger_search)
        grid.addWidget(search_btn, 1, 4)
        reset_btn = QPushButton('重置条件')
        reset_btn.setStyleSheet(self._secondary_style())
        reset_btn.clicked.connect(self.reset_filters)
        grid.addWidget(reset_btn, 1, 5)
        layout.addWidget(filters)
        if self.project_type in LOADER_RELEVANT_TYPES:
            self.loader_combo.setEnabled(True)
        else:
            self.loader_combo.setEnabled(False)
        self.results_list = QListWidget()
        self.results_list.setStyleSheet('\n            QListWidget {\n                background: transparent; border: none; outline: none;\n            }\n            QListWidget::item { border: none; padding: 3px 0; }\n            QListWidget::item:selected { background: transparent; }\n        ')
        self.results_list.setSpacing(4)
        self.results_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        layout.addWidget(self.results_list, 1)
        self.loading_overlay = LoadingOverlay(self.results_list.viewport())
        self.loading_overlay.attach_to(self.results_list.viewport())
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
        self.download_bar.setStyleSheet('\n            QProgressBar { background: #333; border-radius: 4px; height: 12px; }\n            QProgressBar::chunk { background: #0078d4; border-radius: 4px; }\n        ')
        layout.addWidget(self.download_bar)
        self._browser_index = self.stack.addWidget(browser)
        self.stack.setCurrentWidget(browser)

    def _combo_style(self):
        return '\n            QComboBox {\n                background: rgba(255,255,255,0.06);\n                border: 1px solid rgba(255,255,255,0.12);\n                border-radius: 6px; color: #f0f0f0; padding: 3px 8px;\n            }\n            QComboBox::drop-down { border: none; }\n            QComboBox QAbstractItemView { background: #2a2a2a; color: #f0f0f0; }\n        '

    def _primary_style(self):
        return 'QPushButton { background:#0078d4; color:white; border:none; border-radius:6px; padding:6px 16px; } QPushButton:hover { background:#106ebe; }'

    def _secondary_style(self):
        return 'QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:6px 12px; } QPushButton:hover { background:#4a4a4a; }'

    def activate(self):
        if not self._versions_loaded:
            worker = VersionsWorker()
            worker.done.connect(self._on_versions)
            _track_worker(worker)
            self._versions_loaded = True
        if not self._loaded_once:
            self._loaded_once = True
            self.search()
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
        self._trigger_search()

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
        if key in self._categories_loaded:
            self._populate_categories()
            return
        if key in self._categories_loading:
            return
        self._categories_loading.add(key)
        worker = CategoriesWorker(source_name, self.project_type)
        worker.done.connect(self._on_categories)
        _track_worker(worker)

    def _on_categories(self, source_name, project_type, categories):
        key = source_name, project_type
        self._categories[key] = categories or []
        self._categories_loaded.add(key)
        self._categories_loading.discard(key)
        if source_name == self.source_combo.currentText() and project_type == self.project_type:
            self._populate_categories()

    def _populate_categories(self):
        source_name = self.source_combo.currentText()
        categories = self._categories.get((source_name, self.project_type), [])
        current = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem('全部分类', '')
        loader_keys = set(LOADERS.keys())
        for cid, name in categories:
            if self.project_type in LOADER_RELEVANT_TYPES and cid in loader_keys:
                continue
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
        loader = self.loader_combo.currentData() if self.loader_combo.isEnabled() else ''
        category = self.category_combo.currentData() or ''
        sort = self.sort_combo.currentData() or 'relevance'
        self._generation += 1
        gen = self._generation
        self._show_loading('正在加载...')
        offset = (self._page - 1) * self.PAGE_SIZE
        worker = SearchWorker(gen, source_name, query, self.project_type, mc_version, loader, category, sort, offset, self.PAGE_SIZE)
        worker.done.connect(self._on_search_done)
        worker.error.connect(self._on_search_error)
        self._workers.append(worker)
        worker.finished.connect((lambda w=worker: self._release_worker(w)))
        _track_worker(worker)

    def _release_worker(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)

    def _on_search_done(self, generation, result):
        if generation != self._generation:
            return
        self._total = max(0, result.total)
        pages = max(1, math.ceil(self._total / self.PAGE_SIZE))
        if self._page > pages:
            self._page = pages
            self.search()
            return
        self.loading_overlay.finish()
        self.pagination.set_pages(self._page, pages)
        self._render_projects(result.projects)

    def _on_search_error(self, generation, message):
        if generation != self._generation:
            return
        self._total = 0
        self._page = 1
        self.pagination.set_pages(1, 1)
        if is_network_error_text(message) or not network_available():
            reason = '未连接到互联网，无法获取搜索结果'
        else:
            reason = str(message or '').strip() or '获取搜索结果失败'
        self.results_list.clear()
        self._cards.clear()
        self.status_label.setText('')
        self.loading_overlay.fail(reason)

    def _show_offline_placeholder(self):
        self.results_list.clear()
        self._cards.clear()
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
            title = f'{card.project.title} {card.project.translated_title} {card.project.slug}'.lower()
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
        self.results_list.setUpdatesEnabled(False)
        self.results_list.clear()
        self._cards.clear()
        if not projects:
            self.status_label.setText('未找到匹配的内容')
            self.status_label.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
            self.results_list.setUpdatesEnabled(True)
            return
        for project in projects:
            card = ProjectCard(project)
            card.clicked.connect(self._download_project)
            card.download_clicked.connect(self._download_project)
            item = QListWidgetItem()
            item.setSizeHint(QSize(100, 96))
            self.results_list.addItem(item)
            self.results_list.setItemWidget(item, card)
            card._list_item = item
            self._cards.append(card)
            pix = self.icon_loader.cached(project.icon_url)
            if pix:
                card.set_icon(pix)
            elif project.icon_url:
                self.icon_loader.request(project.icon_url)
        self.results_list.setUpdatesEnabled(True)
        self.status_label.setText(f'第 {self._page} 页 · 共 {self._total} 个结果 · 来源 {self.source_combo.currentText()}')
        self.status_label.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')

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
        QMessageBox.information(self, bilingual_plain(project.translated_title, project.title), f'{project.description}\n\n来源: {project.source}\n下载量: {project.downloads:,}')

    def _target_game_dir(self):
        base = Path(self.config.get_mc_dir())
        vid = str(self.config.get('selected_version', '') or '').strip()
        if vid:
            try:
                return self.launch_mgr.get_game_dir(vid)
            except Exception:
                pass
        return base

    def _target_mc_version_from_instance(self):
        vid = str(self.config.get('selected_version', '') or '').strip()
        if not vid:
            return ''
        try:
            path = Path(self.config.get_mc_dir()) / 'versions' / vid / f'{vid}.json'
            data = json.loads(path.read_text(encoding='utf-8'))
            for candidate in data.get('clientVersion'), data.get('inheritsFrom'), vid:
                if candidate:
                    match = re.search('(\\d+\\.\\d+(?:\\.\\d+)?)', str(candidate))
                    if match:
                        return match.group(1)
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
        self.results_list.clear()
        self._cards.clear()
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
        title = sanitize_filename(project.translated_title or project.title or stem, stem)
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
        from core.net import DownloadGate
        if DownloadGate.available() <= 0:
            QMessageBox.warning(self, '下载任务已满', f'当前同时下载任务已达到上限 {DownloadGate.max_threads()}。\n请前往「全局设置 → 下载线程数」调整后再试。')
            return
        mc_version = self._target_mc_version()
        loader = self.loader_combo.currentData() if self.loader_combo.isEnabled() else ''
        game_dir = self._target_game_dir()
        page = ProjectFilesDialog(project, mc_version, loader, game_dir, self.project_type, self, embedded=True)
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
        self._close_files_page()
        if not mod_file or not mod_file.download_url:
            QMessageBox.warning(self, '无法下载', '没有选择文件或文件没有下载地址')
            return
        self._start_project_download(project, mod_file, world, game_dir)

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

    def _start_project_download(self, project, mod_file, world, game_dir):
        if self.project_type == 'modpack':
            dest = PROJECT_TEMP / 'modpacks' / sanitize_filename(mod_file.filename, 'modpack.mrpack')
            self._pending_download = 'modpack', project.title
        else:
            if self.project_type == 'datapack':
                if not world:
                    QMessageBox.warning(self, '无法安装', '请先在游戏内创建世界，再安装数据包')
                    return
                dest_dir = game_dir / 'saves' / world / 'datapacks'
            elif self.project_type == 'resourcepack':
                dest_dir = game_dir / 'resourcepacks'
            elif self.project_type == 'shader':
                dest_dir = game_dir / 'shaderpacks'
            else:
                dest_dir = game_dir / 'mods'
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = unique_path(dest_dir, self._formatted_filename(project, mod_file))
            self._pending_download = 'file', str(dest)
        self._download_expected_size = int(mod_file.size or 0)
        worker = DownloadWorker(mod_file.download_url, dest, mod_file.sha1, mod_file.size)
        worker.status.connect(self.status_label.setText)
        worker.progress.connect(self._on_download_progress)
        worker.done.connect(self._on_download_done)
        worker.error.connect(self._on_download_error)
        self._workers.append(worker)
        worker.finished.connect((lambda w=worker: self._release_worker(w)))
        self.download_bar.show()
        self.download_bar.reset_value()
        _track_worker(worker)

    def _on_download_progress(self, done, total):
        if total <= 0:
            total = int(getattr(self, '_download_expected_size', 0) or 0)
        if total > 0:
            self.download_bar.setRange(0, 100)
            self.download_bar.set_animated_value(min(100, int(done * 100 / total)))
            self.status_label.setText(f'下载中... {done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB')
            return
        self.download_bar.setRange(0, 0)
        self.status_label.setText(f'下载中... {done / 1024 / 1024:.1f} MB')

    def _on_download_done(self, path):
        self.download_bar.hide()
        self.download_bar.setRange(0, 100)
        kind = self._pending_download[0] if self._pending_download else 'file'
        name = self._pending_download[1] if self._pending_download else ''
        self._pending_download = None
        if kind == 'modpack':
            self.status_label.setText('整合包下载完成，准备导入...')
            self.import_pack_requested.emit(path, name)
            return
        self.status_label.setText(f'下载完成: {path}')
        QMessageBox.information(self, '下载完成', f'已保存到：\n{path}')

    def _on_download_error(self, message):
        self.download_bar.hide()
        self._pending_download = None
        if not is_network_error_text(message):
            if not network_available():
                if '校验' not in str(message):
                    self.status_label.setText('未连接到互联网')
                    self.status_label.setStyleSheet('color:rgba(255,255,255,0.5); font-size:12px;')
                    notify_network_failure(self, '下载文件')
                    return
        self.status_label.setText(message)
        self.status_label.setStyleSheet('color: #ff7777; font-size: 12px;')
        QMessageBox.warning(self, '下载失败', message)

    def _choose_local_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择整合包', '', 'Minecraft 整合包 (*.mrpack *.zip);;所有文件 (*.*)')
        if path:
            self.import_pack_requested.emit(path, '')

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
        super().hideEvent(event)

    def shutdown(self):
        try:
            self.loading_overlay.stop()
        except Exception:
            pass
        if self._files_page is not None:
            self._close_files_page()
        self.icon_loader.stop()
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
