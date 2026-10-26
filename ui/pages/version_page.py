'''
版本 / 下载中心页面
- 左侧竖向导航替代原来的横向标签栏
- 已安装版本、下载游戏版本
- 模组 / 整合包 / 数据包 / 资源包 / 光影包 社区资源
- 整合包本地导入
'''

import json
import os
import re
import time
import urllib.request
import requests
import certifi
from pathlib import Path
from PySide6.QtCore import (Qt, Signal, QTimer, QThread, QUrl, QSize)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QPixmap)
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)
from core.app_font import ui_font
from ui.widgets.frameless_dialog import FramelessInputDialog
from core.config import ConfigManager
from core.download_manager import DownloadManager
from core.launch import LaunchManager
from core.modpack import (ModpackImportWorker, inspect_modpack, sanitize_version_name)
from core.version import (FullInstallWorker, VersionManager, VersionDeleteWorker, cleanup_deleted_version, detect_version_loader)
from core.net import is_network_error_text, network_available
from ui.widgets.toast import notify_network_failure
from ui.widgets.loading_overlay import LoadingOverlay
from ui.widgets.smooth_progress import SmoothProgressBar
from .mod_browser_page import ModBrowserPage
from .modpack_import_page import ModpackImportPage
from .version_detail_page import VersionDetailPage

def _natural_key(text: str):
    return [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in re.split('(\\d+)', text)]

_DETACHED_LOADERS = set()

class _SpeedProbeWorker(QThread):
    done = Signal(float)

    def run(self):
        urls = (
            'https://bmclapi2.bangbang93.com/maven/com/google/guava/guava/31.0.1-jre/guava-31.0.1-jre.jar',
            'https://openbmclapi.bangbang93.com/maven/com/google/guava/guava/31.0.1-jre/guava-31.0.1-jre.jar',
        )
        for url in urls:
            started = time.monotonic()
            received = 0
            try:
                response = requests.get(
                    url, headers={'Range': 'bytes=0-524287', 'User-Agent': 'PMGL-Launcher/1.1'},
                    stream=True, timeout=(4, 8), verify=certifi.where(),
                )
                response.raise_for_status()
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received >= 524288:
                        break
                response.close()
                elapsed = max(0.001, time.monotonic() - started)
                if received >= 65536:
                    self.done.emit(received / elapsed)
                    return
            except Exception:
                continue
        self.done.emit(0.0)


class LoadVersionsWorker(QThread):
    finished = Signal(list)
    progress = Signal(int)
    def __init__(self, mirror_urls, version_type):
        super().__init__()
        if isinstance(mirror_urls, str):
            mirror_urls = [mirror_urls]
        self.mirror_urls = [u for u in mirror_urls or [] if u]
        self.version_type = version_type
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _fetch_manifest(self):
        '''并发请求各镜像，谁先返回有效清单就用谁（特殊网络环境下不会逐个超时等待）。'''

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def fetch(base):
            for path in ('/mc/game/version_manifest_v2.json',):
                if self._cancelled:
                    return None
                url = base.rstrip('/') + path
                try:
                    req = urllib.request.Request(
                        url, headers={'User-Agent': 'PMGL-Launcher/1.1'},
                    )
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        data = json.loads(resp.read().decode('utf-8'))
                    if isinstance(data, dict) and data.get('versions'):
                        return data
                except Exception:
                    continue
            return None

        mirrors = [u for u in self.mirror_urls if u] or [
            'https://bmclapi2.bangbang93.com', 'https://launchermeta.mojang.com',
        ]
        pool = ThreadPoolExecutor(max_workers=max(1, len(mirrors)))
        try:
            futures = [pool.submit(fetch, base) for base in mirrors]
            for future in as_completed(futures):
                if self._cancelled:
                    return None
                try:
                    result = future.result()
                except Exception:
                    continue
                if result:
                    for other in futures:
                        other.cancel()
                    return result
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return None

    def run(self):
        versions = []
        try:
            self.progress.emit(5)
            manifest = self._fetch_manifest()
            if self._cancelled:
                return
            if self._cancelled:
                return
            if not manifest:
                self.finished.emit([])
                return
            self.progress.emit(70)
            for v in manifest.get('versions', []):
                if (self.version_type == 'all'
                        or v.get('type') == self.version_type):
                    versions.append(v)
            self.progress.emit(95)
            self.finished.emit(versions)
        except Exception as e:
            print(f'[LoadVersionsWorker] 加载失败: {e}')
            self.finished.emit([])


class VersionValidationWorker(QThread):
    done = Signal(dict)
    def __init__(self, mc_dir, version_ids):
        super().__init__()
        self.mc_dir = mc_dir
        self.version_ids = list(version_ids)

    def run(self):
        result = {}
        try:
            manager = VersionManager(self.mc_dir)
            for vid in self.version_ids:
                try:
                    result[vid] = manager.validate_version(vid)
                except Exception as exc:
                    result[vid] = {'version_id': vid, 'valid': False, 'fatal': True, 'issues': [str(exc)]}
                    continue
        finally:
            self.done.emit(result)


class VersionPage(QWidget):
    version_installed = Signal(str)
    version_selected = Signal(str)
    request_switch_page = Signal(int)
    game_dir_changed = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('VersionPage')
        self.config = ConfigManager()
        self.launch_mgr = LaunchManager()
        self.version_mgr = VersionManager()
        self._install_worker = None
        self._current_install_worker = None
        self._delete_worker = None
        self._current_detail_page = None
        self._current_detail_version_id = None
        self._detail_page_indexes = []
        self._load_worker = None
        self._loading = False
        self._load_gen = 0
        self._available_filter = 'release'
        self._modpack_worker = None
        self._modpack_dialog = None
        self._modpack_page = None
        self._current_modpack_path = ''
        self._download_task_id = ''
        self._install_jobs = {}
        self._install_queue = []
        self._install_probe = None
        self._install_seq = 0
        self._modpack_task_id = ''
        self._modpack_floor = 0.0
        self._nicknames = self.config.get('version_nicknames', {})
        self._refreshing_dirs = False
        self._validation_worker = None
        self._validation = {}
        self._version_items = {}
        self._pending_available_refresh = False
        self._pending_post_install_refresh = False
        self._pending_installed_version = ''
        self._pending_modpack_version = ''
        if not self.config.get('file_download_source'):
            self.config.set('file_download_source', 'auto')
        if not self.config.get('version_list_source'):
            self.config.set('version_list_source', 'auto')
        self.config.save()
        self._sections = {}
        self._init_ui()
        self._refresh_game_dir_combo()
        QTimer.singleShot(50, self._load_installed_versions)
        QTimer.singleShot(100, self._load_available_versions_async)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet('background: transparent;')
        root.addWidget(self.stack)
        self.installed_page = self._build_installed_page()
        self.available_page = self._build_available_page()
        self.mod_browser = ModBrowserPage('mod', self)
        self.modpack_browser = ModBrowserPage('modpack', self)
        self.datapack_browser = ModBrowserPage('datapack', self)
        self.resourcepack_browser = ModBrowserPage('resourcepack', self)
        self.shader_browser = ModBrowserPage('shader', self)
        for browser in self.mod_browser, self.modpack_browser, self.datapack_browser, self.resourcepack_browser, self.shader_browser:
            browser.import_pack_requested.connect(self._start_modpack_import)
            browser.download_game_requested.connect(self._switch_to_version_page)
        pages = [('installed', self.installed_page), ('available', self.available_page), ('mod', self.mod_browser), ('modpack', self.modpack_browser), ('datapack', self.datapack_browser), ('resourcepack', self.resourcepack_browser), ('shader', self.shader_browser)]
        for key, widget in pages:
            self._sections[key] = self.stack.addWidget(widget)
        self._show_section('available')

    def _select_section(self, key):
        self._show_section(key)

    def _show_section(self, key):
        index = self._sections.get(key)
        if index is None:
            return
        self.stack.setCurrentIndex(index)
        if key == 'installed':
            current = self.config.get('selected_game_dir', '') or self.config.get_mc_dir() or ''
            if current and Path(current).is_dir():
                self.version_mgr = VersionManager(current)
            self._refresh_game_dir_combo()
            self._load_installed_versions()
            return
        if key == 'available':
            if self.available_list.count() == 0:
                self._load_available_versions_async()
                return
            return
        if key == 'mod':
            self.mod_browser.activate()
            return
        if key == 'modpack':
            self.modpack_browser.activate()
            return
        if key == 'datapack':
            self.datapack_browser.activate()
            return
        if key == 'resourcepack':
            self.resourcepack_browser.activate()
            return
        if key == 'shader':
            self.shader_browser.activate()

    def switch_to_available_tab(self):
        self._select_section('available')

    def show_section(self, key):
        self._select_section(key)

    def refresh(self):
        self._load_installed_versions()
        self._load_available_versions_async()

    def _build_installed_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(12)
        title = QLabel('已安装版本')
        title.setFont(ui_font(20, QFont.DemiBold))
        title.setStyleSheet('color:#f0f0f0;')
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        self.game_dir_combo = QComboBox()
        self.game_dir_combo.setMinimumWidth(260)
        self.game_dir_combo.setMaximumWidth(430)
        self.game_dir_combo.setToolTip('选择游戏文件夹，切换后自动加载其中的 MC 版本')
        self.game_dir_combo.setStyleSheet('\n            QComboBox { background:rgba(255,255,255,0.06); color:#f0f0f0;\n                        border:1px solid rgba(255,255,255,0.12); border-radius:7px;\n                        padding:5px 10px; }\n            QComboBox:hover { border-color:rgba(255,255,255,0.25); }\n            QComboBox QAbstractItemView { background:#2b2b2b; color:#f0f0f0;\n                        selection-background-color:#0078d4; }\n        ')
        self.game_dir_combo.currentIndexChanged.connect(self._on_game_dir_changed)
        self.game_dir_combo.setContextMenuPolicy(Qt.CustomContextMenu)
        self.game_dir_combo.customContextMenuRequested.connect(self._show_game_dir_menu)
        header.addWidget(self.game_dir_combo)
        self.add_game_dir_btn = QPushButton('+')
        self.add_game_dir_btn.setToolTip('添加游戏文件夹')
        self.add_game_dir_btn.setFixedSize(34, 32)
        self.add_game_dir_btn.setStyleSheet('QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:0; font-size:18px; font-weight:600; } QPushButton:hover { background:#4a4a4a; }')
        self.add_game_dir_btn.clicked.connect(self._on_add_game_dir)
        header.addWidget(self.add_game_dir_btn)
        self.remove_game_dir_btn = QPushButton('✕')
        self.remove_game_dir_btn.setToolTip('从已保存列表中移除（不会删除文件夹）')
        self.remove_game_dir_btn.setFixedWidth(32)
        self.remove_game_dir_btn.setStyleSheet('\n            QPushButton { background:transparent; color:#ff8888; border:1px solid rgba(255,120,120,0.55);\n                          border-radius:6px; padding:5px 8px; }\n            QPushButton:hover { background:rgba(255,80,80,0.18); }\n        ')
        self.remove_game_dir_btn.clicked.connect(self._on_remove_game_dir)
        header.addWidget(self.remove_game_dir_btn)
        layout.addLayout(header)
        hint = QLabel('双击版本可将其设为当前启动版本；也可重命名、删除或打开版本文件夹。游戏文件损坏请到「游戏设置 → 游戏修复」处理。')
        hint.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
        layout.addWidget(hint)
        self.installed_list = QListWidget()
        self.installed_list.setStyleSheet('\n            QListWidget {\n                background: rgba(10,12,18,0.45);\n                border: 1px solid rgba(255,255,255,0.16);\n                border-radius: 10px;\n            }\n            QListWidget::item {\n                padding: 11px 12px;\n                color: rgba(255,255,255,0.85);\n                border-bottom: 1px solid rgba(255,255,255,0.05);\n            }\n            QListWidget::item:selected {\n                background: rgba(122,160,255,0.2);\n                border-left: 3px solid #7aa0ff;\n            }\n            QScrollBar:vertical {\n                background: rgba(255,255,255,0.06);\n                width: 13px;\n                margin: 2px;\n                border-radius: 6px;\n            }\n            QScrollBar::handle:vertical {\n                background: rgba(165,195,255,0.62);\n                min-height: 32px;\n                border-radius: 5px;\n            }\n            QScrollBar::handle:vertical:hover {\n                background: rgba(190,215,255,0.9);\n            }\n            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {\n                height: 0px;\n            }\n            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {\n                background: transparent;\n            }\n        ')
        self.installed_list.setIconSize(QSize(20, 20))
        self.installed_list.itemSelectionChanged.connect(self._on_installed_selection_changed)
        self.installed_list.itemDoubleClicked.connect(self._on_installed_double_clicked)
        layout.addWidget(self.installed_list, 1)
        btn_row = QHBoxLayout()
        self.rename_btn = QPushButton('重命名')
        self.rename_btn.setEnabled(False)
        self.rename_btn.clicked.connect(self._on_rename_version)
        self.rename_btn.setStyleSheet(self._secondary_btn_style())
        self.delete_btn = QPushButton('删除')
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._on_delete_version)
        self.delete_btn.setStyleSheet('\n            QPushButton { background:#6c2a2a; color:white; border:none;\n                          border-radius:6px; padding:6px 16px; }\n            QPushButton:hover { background:#8a3a3a; }\n            QPushButton:disabled { background:#2a2a2a; color:#666; }\n        ')
        self.open_folder_btn = QPushButton('打开版本文件夹')
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self._on_open_version_folder)
        self.open_folder_btn.setStyleSheet(self._secondary_btn_style())
        btn_row.addStretch(1)
        btn_row.addWidget(self.open_folder_btn)
        btn_row.addWidget(self.rename_btn)
        btn_row.addWidget(self.delete_btn)
        layout.addLayout(btn_row)
        return widget

    def _secondary_btn_style(self):
        return '\n            QPushButton { background:#3a3a3a; color:white; border:none;\n                          border-radius:6px; padding:6px 16px; }\n            QPushButton:hover { background:#4a4a4a; }\n            QPushButton:disabled { background:#2a2a2a; color:#666; }\n        '

    @staticmethod
    def _dir_key(path: str) -> str:
        return os.path.normcase(os.path.normpath(str(path)))

    def _saved_game_dirs(self):
        dirs = self.config.get('game_dirs', []) or []
        selected = self.config.get('selected_game_dir', '') or self.config.get_mc_dir() or ''
        result = []
        seen = set()
        for item in ([selected] if selected else []) + list(dirs):
            if not item:
                continue
            path = Path(item)
            if not path.is_dir():
                continue
            key = self._dir_key(str(path))
            if key in seen:
                continue
            seen.add(key)
            result.append(str(path))
        return result

    def sync_game_dir(self):
        '''全局设置里切换游戏目录后，让版本列表与本页选择保持一致。'''

        try:
            path = self.config.get_mc_dir()
        except Exception:
            return
        self.version_mgr = VersionManager(path)
        self._refresh_game_dir_combo(path)
        try:
            self._load_installed_versions()
        except Exception:
            pass

    def _refresh_game_dir_combo(self, select_path: str=''):
        self._refreshing_dirs = True
        self.game_dir_combo.blockSignals(True)
        self.game_dir_combo.clear()
        dirs = self._saved_game_dirs()
        for path in dirs:
            self.game_dir_combo.addItem(path, path)
        if not dirs:
            self.game_dir_combo.addItem('未选择游戏文件夹', '')
        desired = select_path or self.config.get('selected_game_dir', '') or self.config.get_mc_dir() or ''
        index = self.game_dir_combo.findData(desired)
        self.game_dir_combo.setCurrentIndex(index if index >= 0 else 0)
        self.game_dir_combo.blockSignals(False)
        self._refreshing_dirs = False

    def _show_game_dir_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        path = str(self.game_dir_combo.currentData() or '')
        if not path:
            path = str(self.config.get_mc_dir() or '')
        menu = QMenu(self)
        open_action = menu.addAction('打开文件夹位置')
        open_action.setEnabled(bool(path) and Path(path).is_dir())
        menu.addSeparator()
        add_action = menu.addAction('添加目录')
        remove_action = menu.addAction('移除目录')
        remove_action.setEnabled(bool(self.game_dir_combo.currentData()))
        chosen = menu.exec(self.game_dir_combo.mapToGlobal(pos))
        if chosen is open_action and path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif chosen is add_action:
            self._on_add_game_dir()
        elif chosen is remove_action:
            self._on_remove_game_dir()

    def _on_game_dir_changed(self, index):
        if self._refreshing_dirs:
            return
        path = self.game_dir_combo.itemData(index) or ''
        if not path or not Path(path).is_dir():
            return
        if self._dir_key(path) == self._dir_key(self.version_mgr.mc_dir or ''):
            return
        self._apply_game_dir(path)

    def _apply_game_dir(self, path: str):
        self.config.set('selected_game_dir', path, auto_save=False)
        self.config.set('mc_dir', path, auto_save=False)
        self.config.save()
        self.version_mgr = VersionManager(path)
        self._refresh_game_dir_combo(path)
        self._load_installed_versions()
        self.game_dir_changed.emit(path)

    def _on_add_game_dir(self):
        path = QFileDialog.getExistingDirectory(self, '选择游戏文件夹（.minecraft）')
        if not path:
            return
        path = str(Path(path))
        dirs = list(self.config.get('game_dirs', []) or [])
        if not any((self._dir_key(d) == self._dir_key(path) for d in dirs)):
            dirs.append(path)
            self.config.set('game_dirs', dirs)
        self._apply_game_dir(path)

    def _on_remove_game_dir(self):
        path = self.game_dir_combo.currentData() or ''
        if not path:
            return
        dirs = list(self.config.get('game_dirs', []) or [])
        if len(dirs) <= 1:
            QMessageBox.information(self, '无法移除', '至少需要保留一个游戏文件夹。\n这里只会从列表移除，不会删除任何文件。')
            return
        key = self._dir_key(path)
        remaining = [d for d in dirs if self._dir_key(d) != key]
        self.config.set('game_dirs', remaining)
        current = self.config.get('selected_game_dir', '') or self.config.get_mc_dir() or ''
        if self._dir_key(current) == key:
            next_dir = next((d for d in remaining if Path(d).is_dir()), '')
            if not next_dir:
                self.config.set('game_dirs', dirs)
                QMessageBox.information(self, '无法移除', '其余已保存的游戏文件夹不存在，无法切换。\n请先添加一个有效的游戏文件夹。')
                return
            self._apply_game_dir(next_dir)
            return
        self._refresh_game_dir_combo()
        self._load_installed_versions()
        self.game_dir_changed.emit(self.config.get('selected_game_dir', '') or '')

    _LOADER_ICON_FILES = {'forge': 'forge.png', 'fabric': 'fabric.png', 'neoforge': 'neo_forge.png', 'optifine': 'optifine.png'}
    def _version_icon(self, version_type: str, loader: str='', version_id: str='') -> QIcon:
        if version_id:
            version_dir = Path(self.version_mgr.mc_dir) / 'versions' / version_id
            for relative in ('icon.png', 'pack.png', 'logo.png', 'loading/logo.png'):
                icon_path = version_dir / relative
                if not icon_path.is_file():
                    continue
                if QPixmap(str(icon_path)).isNull():
                    continue
                return QIcon(str(icon_path))
        filename = self._LOADER_ICON_FILES.get(str(loader or '').lower())
        if not filename:
            filename = 'Release.png' if str(version_type).lower() == 'release' else 'Snapshoot.png'
        path = Path(__file__).resolve().parent.parent / 'imgs' / filename
        if path.is_file():
            return QIcon(str(path))
        return QIcon()

    def _make_installed_item(self, version_id: str, version_type: str, loader: str):
        display_name = self._nicknames.get(version_id)
        text = f'{display_name}  [{version_id}]' if display_name else version_id
        item = QListWidgetItem(text)
        item.setIcon(self._version_icon(version_type, loader, version_id))
        item.setData(Qt.UserRole, version_id)
        item.setData(Qt.UserRole + 1, text)
        return item

    @staticmethod
    def _empty_installed_item():
        item = QListWidgetItem('暂无已安装版本')
        item.setFlags(Qt.NoItemFlags)
        item.setForeground(QColor(128, 128, 128))
        return item

    def _insert_installed_item(self, item, version_id: str):
        for index in range(self.installed_list.count() - 1, -1, -1):
            if not self.installed_list.item(index).data(Qt.UserRole):
                self.installed_list.takeItem(index)
        key = _natural_key(version_id)
        insert_at = self.installed_list.count()
        for index in range(self.installed_list.count()):
            other_id = str(self.installed_list.item(index).data(Qt.UserRole) or '')
            if other_id and key > _natural_key(other_id):
                insert_at = index
                break
        self.installed_list.insertItem(insert_at, item)
        self._version_items[version_id] = item
        self.installed_list.setCurrentItem(item)

    def _remove_installed_item(self, version_id: str):
        item = self._version_items.pop(version_id, None)
        if item is None:
            for index in range(self.installed_list.count()):
                candidate = self.installed_list.item(index)
                if candidate.data(Qt.UserRole) == version_id:
                    item = candidate
                    break
        row = self.installed_list.row(item) if item is not None else -1
        if item is not None:
            self.installed_list.takeItem(row)
        self._validation.pop(version_id, None)
        if self.installed_list.count() == 0:
            self.installed_list.addItem(self._empty_installed_item())
        else:
            self.installed_list.setCurrentRow(min(max(row, 0), self.installed_list.count() - 1))
        self._on_installed_selection_changed()

    def _refresh_renamed_item(self, old_id: str, new_id: str):
        old_item = self._version_items.pop(old_id, None)
        if old_item is not None:
            self.installed_list.takeItem(self.installed_list.row(old_item))
        self._validation.pop(old_id, None)
        self._nicknames = self.config.get('version_nicknames', {}) or {}
        version_dir = Path(self.version_mgr.mc_dir) / 'versions' / new_id
        data = {}
        try:
            data = json.loads((version_dir / f'{new_id}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        version_type = str(data.get('type') or 'unknown')
        loader = detect_version_loader(new_id, data, version_dir)
        item = self._make_installed_item(new_id, version_type, loader)
        self._insert_installed_item(item, new_id)
        self._start_validation([new_id])

    def _load_installed_versions(self):
        self.installed_list.clear()
        self._validation = {}
        self._version_items = {}
        self._nicknames = self.config.get('version_nicknames', {})
        try:
            versions = self.version_mgr.get_installed_versions()
            versions.sort(
                key=lambda v: _natural_key(str(v.get('id', ''))),
                reverse=True,
            )
            for v in versions:
                vid = v.get('id', 'unknown')
                vtype = v.get('type', '')
                item = self._make_installed_item(
                    vid, vtype, str(v.get('loader') or '')
                )
                self.installed_list.addItem(item)
                self._version_items[vid] = item
            if not versions:
                self.installed_list.addItem(self._empty_installed_item())
            else:
                self.installed_list.setCurrentRow(0)
                self._start_validation(list(self._version_items))
        except Exception as e:
            self.installed_list.addItem(f'加载失败: {e}')
        self._on_installed_selection_changed()

    def _start_validation(self, version_ids):
        old = self._validation_worker
        if old is not None and old.isRunning():
            try:
                old.terminate()
                old.wait(300)
            except RuntimeError:
                pass
        self._validation_worker = VersionValidationWorker(self.version_mgr.mc_dir, version_ids)
        self._validation_worker.done.connect(self._on_validation_done)
        self._validation_worker.finished.connect(self._on_validation_finished)
        self._validation_worker.start()

    def _on_validation_finished(self):
        worker = self.sender()
        if self._validation_worker is worker:
            self._validation_worker = None
        if worker is not None:
            worker.deleteLater()

    def _on_validation_done(self, result: dict):
        self._validation.update(result or {})
        for vid, info in self._validation.items():
            item = self._version_items.get(vid)
            if item is None:
                continue
            base_text = str(item.data(Qt.UserRole + 1) or item.data(Qt.UserRole) or vid)
            item.setText(base_text)
            item.setData(Qt.ForegroundRole, None)
            item.setToolTip('')
            issues = info.get('issues') or []
            if not issues:
                continue
            item.setForeground(QColor('#ff6b6b'))
            if info.get('fatal'):
                item.setText(base_text + '    [错误：不可启动]')
            else:
                item.setText(base_text + '    [需修复]')
            item.setToolTip('；'.join(issues))

    def _on_installed_selection_changed(self):
        item = self.installed_list.currentItem()
        ok = bool(item and item.data(Qt.UserRole))
        self.rename_btn.setEnabled(ok)
        self.delete_btn.setEnabled(ok)
        self.open_folder_btn.setEnabled(ok)

    def _on_installed_double_clicked(self, item):
        version_id = item.data(Qt.UserRole)
        if not version_id:
            return
        info = self._validation.get(version_id)
        if info and info.get('fatal'):
            QMessageBox.warning(self, '版本损坏', f'版本「{version_id}」存在无法自动修复的错误：\n' + '\n'.join(info.get('issues') or ['未知错误']) + '\n\n该版本只能显示，不能选择或启动。请删除后重新安装。')
            return
        self.config.set('selected_version', version_id)
        self.config.save()
        self.version_selected.emit(version_id)

    def _on_open_version_folder(self):
        item = self.installed_list.currentItem()
        if item and item.data(Qt.UserRole):
            self._open_version_folder_for(item.data(Qt.UserRole))

    def _open_version_folder_for(self, version_id):
        path = Path(self.config.get_mc_dir()) / 'versions' / version_id
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    _WINDOWS_RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL', *{f'COM{i}' for i in range(1, 10)}, *{f'LPT{i}' for i in range(1, 10)}}
    def _rename_version_error(self, name: str, current_id: str) -> str:
        '''重命名前的完整校验，包括与任意现有文件夹重名。'''

        name = (name or '').strip()
        if not name:
            return '名称不能为空！'
        if '*' in name:
            return "名称不能包含 '*' 字符！"
        if re.search('[<>:"/\\\\|?*\\x00-\\x1f]', name):
            return '名称不能包含 < > : " / \\ | ? * 等字符！'
        if name.endswith((' ', '.')):
            return '名称不能以空格或点结尾！'
        if name.split('.', 1)[0].strip().upper() in self._WINDOWS_RESERVED_NAMES:
            return '该名称为系统保留名称，请更换！'
        if name.casefold() == str(current_id).casefold():
            return '新名称与当前版本名称相同！'
        versions_dir = Path(self.version_mgr.mc_dir) / 'versions'
        try:
            entries = list(versions_dir.iterdir())
        except OSError:
            entries = []
        for item in entries:
            if item.name.casefold() == name.casefold():
                return '不可与现有文件夹重名！'
        return ''

    def _on_rename_version(self):
        current = self.installed_list.currentItem()
        if not current:
            return
        version_id = current.data(Qt.UserRole)
        if not version_id:
            return
        rename_dialog = FramelessInputDialog(self, '重命名版本', f'请输入新的版本名称（当前：{version_id}）\n名称不能与 versions 目录中任何现有文件夹重名：', version_id, validator=(lambda value: self._rename_version_error(value, version_id)))
        if rename_dialog.exec() != QDialog.Accepted:
            return
        new_name = rename_dialog.value.strip()
        if new_name == version_id:
            return
        if not self.version_mgr.rename_version(version_id, new_name):
            QMessageBox.critical(self, '重命名失败', '无法重命名版本文件夹，请检查权限或目录状态')
            return
        version_settings = self.config.get('version_settings', {})
        if version_id in version_settings:
            version_settings[new_name] = version_settings.pop(version_id)
            self.config.set('version_settings', version_settings)
        if self.config.get('selected_version') == version_id:
            self.config.set('selected_version', new_name)
        favorites = self.config.get('favorited_versions', [])
        if version_id in favorites:
            favorites = [new_name if v == version_id else v for v in favorites]
            self.config.set('favorited_versions', favorites)
        nicknames = self.config.get('version_nicknames', {}) or {}
        if version_id in nicknames:
            nicknames[new_name] = nicknames.pop(version_id)
            self.config.set('version_nicknames', nicknames)
        self.config.save()
        self._refresh_renamed_item(version_id, new_name)
        self.version_installed.emit(new_name)
        QMessageBox.information(self, '成功', f'版本已重命名为：{new_name}')

    def _on_delete_version(self):
        current = self.installed_list.currentItem()
        if not current:
            return
        version_id = current.data(Qt.UserRole)
        if not version_id:
            return
        if self._delete_worker is not None and self._delete_worker.isRunning():
            return
        reply = QMessageBox.question(self, '确认删除', f'确定要删除版本 {version_id} 及其所有文件吗？\n此操作不可撤销！', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        version_dir = Path(self.config.get_mc_dir()) / 'versions' / version_id
        self.delete_btn.setEnabled(False)
        self.delete_btn.setText('删除中...')
        worker = VersionDeleteWorker(version_id, version_dir, self)
        self._delete_worker = worker
        worker.done.connect(self._on_version_delete_done)
        worker.failed.connect(self._on_version_delete_failed)
        worker.finished.connect((lambda w=worker: self._on_version_delete_finished(w)))
        worker.start()

    def _on_version_delete_done(self, version_id):
        cleanup_deleted_version(self.config, version_id)
        self.delete_btn.setText('删除')
        self._remove_installed_item(version_id)
        self.version_installed.emit(version_id)
        QMessageBox.information(self, '成功', f'版本 {version_id} 已删除')

    def _on_version_delete_failed(self, version_id, error):
        self.delete_btn.setText('删除')
        self.delete_btn.setEnabled(self.installed_list.currentItem() is not None)
        QMessageBox.critical(self, '删除失败', f'无法删除版本 {version_id}：{error}\n\n如果游戏正在运行，请先退出游戏后重试。')

    def _on_version_delete_finished(self, worker):
        if self._delete_worker is worker:
            self._delete_worker = None
        worker.deleteLater()

    def _build_available_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(12)
        title = QLabel('下载游戏版本')
        title.setFont(ui_font(20, QFont.DemiBold))
        title.setStyleSheet('color:#f0f0f0;')
        layout.addWidget(title)
        filter_row = QHBoxLayout()
        filter_label = QLabel('版本类型')
        filter_label.setStyleSheet('color: rgba(255,255,255,0.7);')
        self.type_combo = QComboBox()
        self.type_combo.addItems(['正式版 (release)', '快照版 (snapshot)'])
        self.type_combo.currentIndexChanged.connect(self._load_available_versions_async)
        self.type_combo.setFixedWidth(180)
        self.type_combo.setStyleSheet(self._combo_style())
        refresh_btn = QPushButton('刷新')
        refresh_btn.clicked.connect(self._load_available_versions_async)
        refresh_btn.setStyleSheet(self._secondary_btn_style())
        filter_row.addWidget(filter_label)
        filter_row.addWidget(self.type_combo)
        filter_row.addWidget(refresh_btn)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)
        self.available_list = QListWidget()
        self.available_list.setIconSize(QSize(20, 20))
        self.available_list.setStyleSheet(self.installed_list.styleSheet())
        self.available_list.itemDoubleClicked.connect(self._on_version_double_clicked)
        layout.addWidget(self.available_list, 1)
        self.available_loading = LoadingOverlay(self.available_list.viewport())
        self.available_loading.attach_to(self.available_list.viewport())
        hint_label = QLabel('双击版本进入详情页，可选择 Fabric / Forge / NeoForge / OptiFine')
        hint_label.setStyleSheet('color: rgba(255,255,255,0.4); font-size: 12px;')
        layout.addWidget(hint_label)
        progress_group = QGroupBox()
        progress_group.setStyleSheet('\n            QGroupBox {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.22);\n                border-radius: 8px;\n                padding-top: 8px;\n            }\n        ')
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setContentsMargins(12, 12, 12, 12)
        self.download_status_label = QLabel('就绪')
        self.download_status_label.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 13px;')
        progress_layout.addWidget(self.download_status_label)
        self.download_progress_bar = SmoothProgressBar()
        self.download_progress_bar.setRange(0, 100)
        self.download_progress_bar.reset_value()
        self.download_progress_bar.hide()
        self.download_progress_bar.setStyleSheet('\n            QProgressBar { background:#333; border-radius:4px; height:16px; }\n            QProgressBar::chunk { background:#0078d4; border-radius:4px; }\n        ')
        progress_layout.addWidget(self.download_progress_bar)
        self.cancel_download_btn = QPushButton('取消下载')
        self.cancel_download_btn.hide()
        self.cancel_download_btn.clicked.connect(self._on_cancel_download)
        self.cancel_download_btn.setStyleSheet('\n            QPushButton { background:#6c2a2a; color:white; border:none;\n                          border-radius:4px; padding:4px 8px; }\n            QPushButton:hover { background:#8a3a3a; }\n        ')
        progress_layout.addWidget(self.cancel_download_btn)
        layout.addWidget(progress_group)
        return widget

    def _combo_style(self):
        return '\n            QComboBox {\n                background: rgba(255,255,255,0.05);\n                border: 1px solid rgba(255,255,255,0.1);\n                border-radius: 6px; color:#f0f0f0; padding:4px 8px;\n            }\n            QComboBox::drop-down { border:none; }\n            QComboBox QAbstractItemView { background:#2a2a2a; color:#f0f0f0; }\n        '

    def _install_busy(self) -> bool:
        for job in list(self._install_jobs.values()):
            worker = job.get('worker')
            try:
                if worker is not None and worker.isRunning() and not getattr(worker, '_cancelled', False):
                    return True
            except RuntimeError:
                continue
        worker = self._modpack_worker
        try:
            if worker is not None and worker.isRunning():
                return True
        except RuntimeError:
            pass
        return False

    def _suspend_available_refresh(self):
        '''安装/导入开始后停止正在进行的版本清单请求，完成后再刷新。'''

        self._pending_available_refresh = True
        self._load_gen += 1
        old = self._load_worker
        if old is not None:
            try:
                if old.isRunning():
                    old.cancel()
                    old.setParent(None)
                    _DETACHED_LOADERS.add(old)
                    old.finished.connect((lambda w=old: _DETACHED_LOADERS.discard(w)))
                    old.finished.connect(old.deleteLater)
            except RuntimeError:
                pass
            self._load_worker = None
        self._loading = False

    def _load_available_versions_async(self):
        if self._install_busy():
            self._pending_available_refresh = True
            return
        self._load_gen += 1
        gen = self._load_gen
        old = self._load_worker
        if old is not None and old.isRunning():
            try:
                old.cancel()
                old.setParent(None)
                _DETACHED_LOADERS.add(old)
                old.finished.connect((lambda w=old: _DETACHED_LOADERS.discard(w)))
                old.finished.connect(old.deleteLater)
            except RuntimeError:
                pass
        self._load_worker = None
        self._loading = False
        self.available_list.clear()
        self.available_loading.start('正在加载版本列表', 0)
        mirror_urls = self.config.get_version_mirrors() or ['https://launchermeta.mojang.com', 'https://bmclapi2.bangbang93.com', 'https://openbmclapi.bangbang93.com']
        type_map = {0: 'release', 1: 'snapshot'}
        vtype = type_map.get(self.type_combo.currentIndex(), 'release')
        self._available_filter = vtype
        self._loading = True
        self._load_worker = LoadVersionsWorker(mirror_urls, 'all')
        self._load_worker.progress.connect(self.available_loading.set_progress)
        self._load_worker.finished.connect((lambda versions, g=gen: self._on_versions_loaded(versions, g)))
        self._load_worker.start()

    def _add_available_header(self, text: str):
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        item.setForeground(QColor(125, 125, 125))
        font = item.font()
        font.setBold(True)
        font.setPointSize(max(8, font.pointSize() - 1))
        item.setFont(font)
        self.available_list.addItem(item)

    def _add_available_version(self, version: dict, tag: str=''):
        version_id = str(version.get('id') or '')
        text = f'{version_id}    {tag}' if tag else version_id
        item = QListWidgetItem(text)
        item.setIcon(self._version_icon(str(version.get('type') or '')))
        item.setData(Qt.UserRole, version_id)
        if tag:
            item.setForeground(QColor('#7fc4ff'))
            item.setToolTip(tag.strip('[]'))
        self.available_list.addItem(item)

    def _on_versions_loaded(self, versions, generation=None):
        if generation is not None and generation != self._load_gen:
            return
        self._loading = False
        self.available_list.clear()
        if not versions:
            offline = not network_available()
            reason = (
                '未连接到互联网，无法获取版本列表'
                if offline else '获取版本列表失败，请检查网络连接'
            )
            self.available_loading.fail(reason)
            if self._load_worker:
                self._load_worker.deleteLater()
                self._load_worker = None
            return
        self.available_loading.finish()
        def _version_order(v):
            return str(v.get('releaseTime') or v.get('time') or ''), _natural_key(str(v.get('id', '')))

        versions = sorted(versions, key=_version_order, reverse=True)
        filter_type = getattr(self, '_available_filter', 'release')
        latest_release = next((v for v in versions if str(v.get('type', '')).lower() == 'release'), None)
        latest_snapshot = next((v for v in versions if str(v.get('type', '')).lower() == 'snapshot'), None)
        pinned = [v for v in (latest_release, latest_snapshot) if v is not None]
        if pinned:
            self._add_available_header('最新版本')
            for version in pinned:
                self._add_available_version(version)
        if filter_type == 'snapshot':
            items = [v for v in versions if str(v.get('type', '')).lower() == 'snapshot']
            if items:
                self._add_available_header(f'快照与预览版 ({len(items)})')
        else:
            items = [v for v in versions if str(v.get('type', '')).lower() == 'release']
            if items:
                self._add_available_header(f'正式版 ({len(items)})')
        for v in items:
            self._add_available_version(v)
        if self._load_worker:
            self._load_worker.deleteLater()
            self._load_worker = None

    def _on_version_double_clicked(self, item):
        version_id = item.data(Qt.UserRole)
        if not version_id:
            return
        self.show_detail_page(version_id)

    def show_detail_page(self, version_id):
        self._current_detail_version_id = version_id
        main_window = self.window()
        stack = getattr(main_window, 'page_stack', None)
        if stack is None:
            parent = self.parent()
            while parent and not hasattr(parent, 'page_stack'):
                parent = parent.parent()
            stack = getattr(parent, 'page_stack', None)
        if stack is None:
            QMessageBox.warning(self, '错误', '无法找到页面堆叠，请检查')
            return
        detail_page = VersionDetailPage(version_id, self)
        self._current_detail_page = detail_page
        detail_page.back_requested.connect((lambda: self._close_detail_page(detail_page, stack, switch_to_version=True)))
        detail_page.download_requested.connect(self._on_download_requested)
        idx = stack.addWidget(detail_page)
        self._detail_page_indexes.append(idx)
        stack.setCurrentIndex(idx)

    def open_detail_page(self, version_id):
        self.show_detail_page(version_id)

    def _close_detail_page(self, detail_page, stack, switch_to_version=True):
        try:
            detail_page.shutdown()
        except Exception:
            pass
        idx = stack.indexOf(detail_page)
        if idx >= 0:
            stack.removeWidget(detail_page)
            detail_page.deleteLater()
            if idx in self._detail_page_indexes:
                self._detail_page_indexes.remove(idx)
        self._current_detail_page = None
        if switch_to_version:
            self._switch_to_version_page()

    def _switch_to_version_page(self):
        self._select_section('available')
        main_window = self.window()
        if main_window and hasattr(main_window, 'show_download_section'):
            main_window.show_download_section('available')
        elif main_window and hasattr(main_window, 'nav_list'):
            main_window.nav_list.setCurrentRow(1)
        if main_window:
            if hasattr(main_window, 'page_stack'):
                stack = main_window.page_stack
                for i in range(stack.count()):
                    if stack.widget(i) is self:
                        stack.setCurrentIndex(i)
                        break

    def _on_download_requested(self, version_name, selections):
        if self._current_detail_page:
            main_window = self.window()
            stack = getattr(main_window, 'page_stack', None)
            if stack:
                self._close_detail_page(self._current_detail_page, stack, switch_to_version=False)
        self._select_section('available')
        version_id = self._current_detail_version_id
        if not version_id:
            QMessageBox.warning(self, '错误', '无法获取版本ID')
            return
        self._suspend_available_refresh()
        mc_dir = self.config.get_mc_dir()
        mirror_url = self.config.get_mirror_url() or 'https://bmclapi2.bangbang93.com'
        loader_name = ''
        for key, label in (('fabric', 'Fabric'), ('forge', 'Forge'), ('neoforge', 'NeoForge'), ('optifine', 'OptiFine')):
            if selections.get(key):
                loader_name = label
                break
        steps = ['下载原版 JSON 文件', '下载原版支持库文件', '下载原版资源文件']
        if loader_name:
            steps.extend([f'下载 {loader_name} 主文件', f'安装 {loader_name}'])
        steps.extend(['安装游戏', '下载游戏支持库文件'])
        has_fabric_api = bool(selections.get('fabric_api') and selections.get('fabric'))
        if has_fabric_api:
            steps.append('下载 Fabric API')
        self._install_seq += 1
        task_id = f'install-{version_id}-{int(time.time() * 1000)}-{self._install_seq}'
        phase_steps = {'json': 0, 'libs': 1, 'assets': 2}
        if loader_name:
            phase_steps.update({'loader_file': 3, 'loader_install': 4, 'game': 5, 'game_libs': 6})
        else:
            phase_steps.update({'game': 3, 'game_libs': 4})
        if has_fabric_api:
            phase_steps['fabric_api'] = 7
        job = {
            'id': task_id, 'task_id': task_id, 'version_id': version_id,
            'mc_dir': mc_dir, 'version_name': version_name, 'selections': selections,
            'mirror': mirror_url, 'steps': steps, 'phase_steps': phase_steps,
            'step_percent': {}, 'worker': None,
        }
        active = self._active_install_jobs()
        if len(active) >= 3:
            self._offer_install_queue(job, '最多同时进行 3 个下载任务，继续添加会进入队列。', poor=False)
            return
        if active:
            self._probe_then_start(job)
            return
        self._start_install_job(job)

    def _active_install_jobs(self):
        return [job for job in self._install_jobs.values() if job.get('worker') is not None]

    def _offer_install_queue(self, job, text, poor=True):
        from ui.widgets.dark_dialog import DarkQuestionDialog
        if poor:
            text += '\n\n当前网络不佳，强行下载会拖慢整体下载速度。'
        dialog = DarkQuestionDialog(
            self, '下载任务', text,
            actions=[('取消', 'cancel', False), ('加入队列', 'queue', True)],
        )
        dialog.exec()
        if dialog.choice == 'queue':
            self._install_queue.append(job)
            self.download_status_label.setText(f'{job["version_name"]} 已加入下载队列')
            return True
        self.download_status_label.setText('已取消本次下载')
        return False

    def _probe_then_start(self, job):
        if self._install_probe is not None and self._install_probe.isRunning():
            self._install_queue.append(job)
            self.download_status_label.setText(f'{job["version_name"]} 已加入下载队列')
            return
        self.download_status_label.setText('正在检测网络速度...')
        probe = _SpeedProbeWorker()
        self._install_probe = probe
        probe.done.connect((lambda speed, j=job: self._on_install_probe_done(j, speed)))
        probe.finished.connect((lambda p=probe: self._clear_install_probe(p)))
        probe.start()

    def _clear_install_probe(self, probe):
        if self._install_probe is probe:
            self._install_probe = None
        probe.deleteLater()

    def _on_install_probe_done(self, job, speed):
        if job in self._install_queue:
            return
        if speed >= 800 * 1024:
            self._start_install_job(job)
            return
        self._offer_install_queue(job, '检测到当前网络速度较慢。', poor=True)

    def _start_install_job(self, job):
        if job.get('worker') is not None:
            return
        if len(self._active_install_jobs()) >= 3:
            self._install_queue.append(job)
            self.download_status_label.setText(f'{job["version_name"]} 已加入下载队列')
            return
        task_id = job['task_id']
        self._download_task_id = task_id
        self._download_phase_steps = dict(job['phase_steps'])
        self._download_step_percent = {}
        self.download_status_label.setText(f'正在准备安装 {job["version_name"]} ...')
        self.download_progress_bar.show()
        self.download_progress_bar.reset_value()
        self.cancel_download_btn.show()
        self.cancel_download_btn.setEnabled(True)
        DownloadManager.instance().start_task(task_id, f'{job["version_name"]} 安装', steps=job['steps'], cancel_callback=(lambda: None))
        DownloadManager.instance().update_step(task_id, 0, state='active')
        worker = FullInstallWorker(job['version_id'], job['mc_dir'], job['version_name'], job['selections'], job['mirror'])
        job['worker'] = worker
        self._install_jobs[job['id']] = job
        self._current_install_worker = worker
        DownloadManager.instance().set_cancel_callback(task_id, worker.cancel)
        worker.status_updated.connect((lambda text, j=job: self._job_status(j, text)))
        worker.progress_updated.connect((lambda value, j=job: self._job_progress(j, value)))
        worker.phase_updated.connect((lambda key, value, j=job: self._job_phase(j, key, value)))
        worker.finished_success.connect((lambda name, j=job: self._job_success(j, name)))
        worker.finished_error.connect((lambda message, j=job: self._job_error(j, message)))
        worker.finished.connect((lambda w=worker, j=job: self._job_finished(j, w)))
        worker.start()
        self._job_status(job, '正在准备安装...')

    def _job_is_latest(self, job):
        return job.get('worker') is self._current_install_worker

    def _job_status(self, job, text):
        if job.get('task_id'):
            DownloadManager.instance().update_task(job['task_id'], status=text)
        if self._job_is_latest(job):
            self.download_status_label.setText(text)

    def _job_phase(self, job, key, percent):
        manager = DownloadManager.instance()
        index = job.get('phase_steps', {}).get(str(key))
        if index is None:
            return
        task = manager.task(job['task_id'])
        if not task:
            return
        steps = task.get('steps', [])
        if index < 0 or index >= len(steps):
            return
        try:
            value = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            return
        previous = job['step_percent'].get(index, 0.0)
        value = max(previous, value)
        job['step_percent'][index] = value
        state = 'done' if value >= 99.9 else 'active'
        if steps[index].get('state') == 'done' and state != 'done':
            state, value = 'done', 100.0
        manager.update_step(job['task_id'], index, percent=value, state=state)

    def _job_progress(self, job, progress):
        if job.get('task_id'):
            DownloadManager.instance().update_task(job['task_id'], percent=float(progress))
        if self._job_is_latest(job):
            self.download_progress_bar.set_animated_value(progress)

    def _job_success(self, job, version_name):
        if job.get('task_id'):
            DownloadManager.instance().finish_task(job['task_id'], True, f'{version_name} 安装完成')
        self._pending_post_install_refresh = True
        self._pending_installed_version = version_name
        from ui.widgets.toast import show_toast
        if self._job_is_latest(job):
            self.download_progress_bar.hide()
            self.cancel_download_btn.hide()
            self.download_status_label.setText('安装完成！')
            from ui.widgets.dark_dialog import DarkQuestionDialog
            dialog = DarkQuestionDialog(self.window(), '安装成功', f'版本：{version_name} 安装成功！', buttons=QMessageBox.Ok, icon_text='✓', icon_color='#2e9e5b')
            dialog.exec()
        else:
            show_toast(f'{version_name} 安装成功', 'success', self)

    def _job_error(self, job, error_msg):
        if job.get('task_id'):
            DownloadManager.instance().finish_task(job['task_id'], False, error_msg)
        self._pending_post_install_refresh = True
        from ui.widgets.toast import show_toast
        if not self._job_is_latest(job):
            show_toast(f'{job["version_name"]} 安装失败', 'error', self)
            return
        self.download_progress_bar.hide()
        self.cancel_download_btn.hide()
        if '取消' in error_msg:
            self.download_status_label.setText('安装已取消，未完成的版本不会出现在已安装列表中')
            return
        self.download_status_label.setText('安装失败: ' + error_msg)
        if is_network_error_text(error_msg) or not network_available():
            self.download_status_label.setText('未连接到互联网')
            notify_network_failure(self, '下载文件')
            return
        QMessageBox.critical(self, '安装失败', error_msg)

    def _job_finished(self, job, worker):
        self._install_jobs.pop(job.get('id'), None)
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        if self._current_install_worker is worker:
            self._current_install_worker = None
        if self._pending_post_install_refresh:
            self._pending_post_install_refresh = False
            self._pending_available_refresh = False
            self._load_installed_versions()
            self._load_available_versions_async()
        elif self._pending_available_refresh:
            self._pending_available_refresh = False
            self._load_available_versions_async()
        name = self._pending_installed_version
        self._pending_installed_version = ''
        if name:
            self.version_installed.emit(name)
        if self._install_queue and len(self._active_install_jobs()) < 3:
            next_job = self._install_queue.pop(0)
            self._start_install_job(next_job)

    def _on_install_status(self, status):
        self.download_status_label.setText(status)
        if self._download_task_id:
            DownloadManager.instance().update_task(self._download_task_id, status=status)

    def _on_install_phase(self, key, percent):
        '''某个具体步骤（JSON/依赖库/资源/加载器等）的实时进度。'''

        manager = DownloadManager.instance()
        index = getattr(self, '_download_phase_steps', {}).get(str(key))
        if index is None or not self._download_task_id:
            return
        task = manager.task(self._download_task_id)
        if not task:
            return
        steps = task.get('steps', [])
        if index < 0 or index >= len(steps):
            return
        try:
            value = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            return
        previous = self._download_step_percent.get(index, 0.0)
        value = max(previous, value)
        self._download_step_percent[index] = value
        state = 'done' if value >= 99.9 else 'active'
        if steps[index].get('state') == 'done' and state != 'done':
            state = 'done'
            value = 100.0
        manager.update_step(self._download_task_id, index, percent=value, state=state)

    def _on_install_progress(self, progress):
        self.download_progress_bar.set_animated_value(progress)
        if self._download_task_id:
            manager = DownloadManager.instance()
            manager.update_task(self._download_task_id, percent=float(progress))

    def _on_install_success(self, version_name):
        if self._download_task_id:
            DownloadManager.instance().finish_task(self._download_task_id, True, f'{version_name} 安装完成')
        # 安装完成后不再自动切换选中版本，由用户手动选择
        self.config.save()
        self.download_progress_bar.hide()
        self.cancel_download_btn.hide()
        self.download_status_label.setText('安装完成！')
        from ui.widgets.dark_dialog import DarkQuestionDialog
        dialog = DarkQuestionDialog(self.window(), '安装成功', f'版本：{version_name} 安装成功！', buttons=QMessageBox.Ok, icon_text='✓', icon_color='#2e9e5b')
        dialog.exec()
        self._pending_post_install_refresh = True
        self._pending_installed_version = version_name

    def _on_install_error(self, error_msg):
        if self._download_task_id:
            DownloadManager.instance().finish_task(self._download_task_id, False, error_msg)
        self.download_progress_bar.hide()
        self.cancel_download_btn.hide()
        self._pending_post_install_refresh = True
        if '取消' in error_msg:
            self.download_status_label.setText('安装已取消，未完成的版本不会出现在已安装列表中')
            return
        self.download_status_label.setText('安装失败: ' + error_msg)
        if is_network_error_text(error_msg) or not network_available():
            self.download_status_label.setText('未连接到互联网')
            notify_network_failure(self, '下载文件')
            return
        QMessageBox.critical(self, '安装失败', error_msg)

    def _on_install_thread_finished(self):
        worker = self._current_install_worker
        if worker is not None:
            worker.deleteLater()
            self._current_install_worker = None
        if self._pending_post_install_refresh:
            self._pending_post_install_refresh = False
            self._pending_available_refresh = False
            self._load_installed_versions()
            self._load_available_versions_async()
        elif self._pending_available_refresh:
            self._pending_available_refresh = False
            self._load_available_versions_async()
        version_name = self._pending_installed_version
        self._pending_installed_version = ''
        self.version_installed.emit(version_name or '')

    def _on_cancel_download(self):
        if self._current_install_worker and self._current_install_worker.isRunning():
            self._current_install_worker.cancel()
            self.cancel_download_btn.setEnabled(False)
            self.download_status_label.setText('正在取消...')
            return
        self.download_status_label.setText('没有正在进行的安装')
        if self._current_install_worker:
            self._current_install_worker.deleteLater()
            self._current_install_worker = None

    def _choose_local_modpack(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择整合包', '', 'Minecraft 整合包 (*.mrpack *.zip);;所有文件 (*.*)')
        if path:
            self._start_modpack_import(path, '')

    def _unique_modpack_name(self, base: str) -> str:
        from core.version import INCOMPLETE_MARKER
        versions_dir = Path(self.config.get_mc_dir()) / 'versions'
        base = sanitize_version_name(base) or '整合包'
        target = versions_dir / base
        if not target.exists() or (target / INCOMPLETE_MARKER).exists():
            return base
        index = 2
        while True:
            candidate = f'{base} ({index})'
            path = versions_dir / candidate
            if not path.exists() or (path / INCOMPLETE_MARKER).exists():
                return candidate
            index += 1

    def _start_modpack_import(self, path, suggested_name=''):
        if self._modpack_worker and self._modpack_worker.isRunning():
            QMessageBox.warning(self, '请稍候', '已有整合包正在导入，请等待完成')
            return
        try:
            info = inspect_modpack(path)
        except Exception as exc:
            QMessageBox.critical(self, '无法识别整合包', str(exc))
            return
        default = self._unique_modpack_name(suggested_name or info.name)
        if self._modpack_page is not None:
            self.stack.removeWidget(self._modpack_page)
            self._modpack_page.deleteLater()
        page = ModpackImportPage(info, path, default, self)
        page.back_requested.connect(self._close_modpack_import_page)
        page.start_requested.connect(self._begin_modpack_import)
        self._modpack_page = page
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    def _begin_modpack_import(self, name):
        page = self._modpack_page
        if page is None:
            return
        if self._modpack_worker and self._modpack_worker.isRunning():
            page.show_error('已有整合包正在导入，请稍候')
            return
        name = sanitize_version_name(name)
        if not name:
            page.show_error('版本名称不能为空')
            return
        target = Path(self.config.get_mc_dir()) / 'versions' / name
        from core.version import INCOMPLETE_MARKER
        if target.exists() and not (target / INCOMPLETE_MARKER).exists():
            page.show_error(f'版本名称「{name}」已存在，请更换名称')
            return
        self._suspend_available_refresh()
        page.set_running(True)
        page.set_status('正在准备导入整合包...')
        self._current_modpack_path = str(page.path)
        import time as _time
        self._modpack_task_id = f'modpack-{int(_time.time() * 1000)}'
        self._modpack_floor = 3.0
        DownloadManager.instance().start_task(self._modpack_task_id, f'{name} 整合包导入', steps=['解析整合包', '安装依赖', '下载并安装整合包文件', '写入版本配置'], cancel_callback=(lambda: None))
        DownloadManager.instance().update_step(self._modpack_task_id, 0, state='active')
        DownloadManager.instance().update_task(self._modpack_task_id, percent=3.0)
        worker = ModpackImportWorker(str(page.path), self.config.get_mc_dir(), name, self)
        self._modpack_worker = worker
        DownloadManager.instance().set_cancel_callback(self._modpack_task_id, worker.cancel)
        worker.status_updated.connect(self._on_modpack_status)
        worker.progress_updated.connect(self._on_modpack_progress)
        worker.stats_updated.connect(self._on_modpack_stats)
        worker.log_message.connect((lambda s: print(f'[整合包] {s}')))
        worker.finished_ok.connect(self._on_modpack_ok)
        worker.finished_error.connect(self._on_modpack_error)
        worker.finished.connect(self._on_modpack_thread_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._open_modpack_download_manager()

    def _open_modpack_download_manager(self):
        self._show_section('modpack')
        window = self.window()
        show_manager = getattr(window, 'show_download_manager_page', None)
        if callable(show_manager):
            show_manager()

    def _close_modpack_import_page(self):
        worker = self._modpack_worker
        if worker and worker.isRunning():
            worker.cancel()
        # 保留 worker 引用直到 finished 信号触发：
        # 否则取消后立刻再导入会与旧任务并发写同一个版本目录，导致未响应/崩溃
        page = self._modpack_page
        self._modpack_page = None
        if page is not None:
            self.stack.removeWidget(page)
            page.deleteLater()
        self._show_section('modpack')

    def _on_modpack_status(self, text):
        if self._modpack_page is not None:
            self._modpack_page.set_status(text)
        if not self._modpack_task_id:
            return
        manager = DownloadManager.instance()
        task = manager.task(self._modpack_task_id)
        if not task:
            return
        lowered = str(text or '')
        floor = self._modpack_floor
        if any((key in lowered for key in ('解析', '准备', '读取'))):
            floor = max(floor, 5.0)
        elif any((key in lowered for key in ('补全', '依赖', '安装', '预下载', '加载器'))):
            floor = max(floor, 15.0)
        elif any((key in lowered for key in ('下载整合包', '下载内容', '下载文件'))):
            floor = max(floor, 55.0)
        elif '写入' in lowered or '配置' in lowered:
            floor = max(floor, 90.0)
        self._modpack_floor = floor
        current = max(float(task.get('percent', 0)), floor)
        self._update_modpack_steps(manager, task, current)
        manager.update_task(self._modpack_task_id, status=text, percent=current)

    def _update_modpack_steps(self, manager, task, percent):
        steps = [dict(step) for step in task.get('steps', [])]
        ranges = [(0, 15), (15, 55), (55, 94), (94, 100)]
        for index, step in enumerate(steps):
            if index >= len(ranges):
                continue
            start, end = ranges[index]
            if percent >= end:
                step['percent'] = 100.0
                step['state'] = 'done'
            elif percent <= start:
                step['percent'] = 0.0
                step['state'] = 'pending'
            else:
                step['percent'] = round((percent - start) * 100 / max(1, end - start), 1)
                step['state'] = 'active'
        if not any((step.get('state') == 'active' for step in steps)):
            for step in steps:
                if step.get('state') == 'pending':
                    step['state'] = 'active'
                    break
        manager.update_task(self._modpack_task_id, steps=steps)

    def _on_modpack_progress(self, value):
        if self._modpack_page is not None:
            self._modpack_page.set_progress(value)
        if not self._modpack_task_id:
            return
        manager = DownloadManager.instance()
        task = manager.task(self._modpack_task_id)
        if not task:
            return
        effective = max(float(value), self._modpack_floor)
        self._update_modpack_steps(manager, task, effective)
        manager.update_task(self._modpack_task_id, percent=effective)

    def _on_modpack_stats(self, speed: str, remaining: str):
        if not self._modpack_task_id:
            return
        DownloadManager.instance().update_task(self._modpack_task_id, speed=speed, remaining=remaining)

    def _on_modpack_ok(self, version_id):
        if self._modpack_task_id:
            DownloadManager.instance().finish_task(self._modpack_task_id, True, '整合包导入完成')
            self._modpack_task_id = ''
        # 整合包导入完成后不再自动切换选中版本，由用户手动选择
        settings = self.config.get('version_settings', {}) or {}
        entry = settings.get(version_id, {}) or {}
        entry['isolated'] = True
        settings[version_id] = entry
        self.config.set('version_settings', settings)
        self.config.save()
        if self._modpack_page is not None:
            self._modpack_page.show_success(version_id)
        self._pending_modpack_version = version_id
        try:
            p = Path(self._current_modpack_path)
            if p.exists() and 'temp_install' in p.parts:
                p.unlink(missing_ok=True)
        except OSError:
            pass
        self._current_modpack_path = ''

    def _on_modpack_thread_finished(self):
        worker = self.sender()
        if self._modpack_worker is worker:
            self._modpack_worker = None
        version_id = self._pending_modpack_version
        self._pending_modpack_version = ''
        self._load_installed_versions()
        self.version_installed.emit(version_id or '')
        if self._pending_available_refresh:
            self._pending_available_refresh = False
            self._load_available_versions_async()

    def _on_modpack_error(self, message):
        if self._modpack_task_id:
            DownloadManager.instance().finish_task(self._modpack_task_id, False, message or '导入失败')
            self._modpack_task_id = ''
        network_problem = bool(message) and (is_network_error_text(message) or not network_available())
        page = self._modpack_page
        if page is not None and page.isVisible():
            page.show_error('因为网络问题导致下载文件失败！' if network_problem else message)
            if network_problem:
                notify_network_failure(self, '下载文件')
                return
            return
        if message and '取消' not in message:
            if network_problem:
                notify_network_failure(self, '下载文件')
                return
            QMessageBox.critical(self, '导入失败', message)

    def has_active_tasks(self) -> bool:
        workers = [job.get('worker') for job in list(self._install_jobs.values())]
        workers.append(self._current_install_worker)
        workers.append(self._modpack_worker)
        for worker in workers:
            try:
                if worker is not None and worker.isRunning() and not getattr(worker, '_cancelled', False):
                    return True
            except RuntimeError:
                continue
        for browser in self.mod_browser, self.modpack_browser, self.datapack_browser, self.resourcepack_browser, self.shader_browser:
            try:
                if browser.has_active_downloads():
                    return True
            except Exception:
                continue
        return False

    def shutdown(self):
        try:
            if self._current_detail_page is not None:
                self._current_detail_page.shutdown()
        except Exception:
            pass
        for browser in self.mod_browser, self.modpack_browser, self.datapack_browser, self.resourcepack_browser, self.shader_browser:
            try:
                browser.shutdown()
            except Exception:
                continue
        if self._modpack_worker and self._modpack_worker.isRunning():
            try:
                self._modpack_worker.cancel()
                if not self._modpack_worker.wait(2000):
                    self._modpack_worker.terminate()
                    self._modpack_worker.wait(500)
            except RuntimeError:
                pass
        if self._load_worker and self._load_worker.isRunning():
            try:
                self._load_worker.cancel()
                if not self._load_worker.wait(1200):
                    self._load_worker.terminate()
                    self._load_worker.wait(300)
            except RuntimeError:
                pass
        validation_worker = self._validation_worker
        if validation_worker and validation_worker.isRunning():
            try:
                validation_worker.terminate()
                validation_worker.wait(500)
            except RuntimeError:
                pass
            self._validation_worker = None
        for worker in list(_DETACHED_LOADERS):
            try:
                if worker.isRunning() and not worker.wait(800):
                    worker.terminate()
                    worker.wait(300)
            except RuntimeError:
                continue
        _DETACHED_LOADERS.clear()
        workers = [job.get('worker') for job in list(self._install_jobs.values())]
        workers.append(self._current_install_worker)
        for worker in workers:
            if worker is None:
                continue
            try:
                if not worker.isRunning():
                    continue
                worker.cancel()
                if not worker.wait(2000):
                    worker.terminate()
                    worker.wait(500)
            except RuntimeError:
                continue
        probe = self._install_probe
        if probe is not None and probe.isRunning():
            try:
                probe.terminate()
                probe.wait(300)
            except RuntimeError:
                pass
        self._install_probe = None

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


