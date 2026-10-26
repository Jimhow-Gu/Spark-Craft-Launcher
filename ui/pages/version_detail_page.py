'''
版本详情页面 - 显示版本信息和加载器选择
进入页面时先显示UI，后台线程加载版本列表
'''

import json
import re
import sys
import time
from concurrent.futures import (FIRST_COMPLETED, ThreadPoolExecutor, wait)
import requests
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QScrollArea, QMessageBox, QSpacerItem, QSizePolicy, QListWidget, QListWidgetItem, QFrame)
from pathlib import Path
from PySide6.QtCore import (Qt, Signal, QThread, QSize, QEasingCurve, QPropertyAnimation)
from PySide6.QtGui import QFont, QIcon
from core.app_font import ui_font
from core.net import network_available
from ui.version_meta import loader_supported, version_tuple
from ui.widgets.back_button import BackButton
from ui.widgets.loading_overlay import LoadingOverlay
from ui.widgets.painted_icons import painted_icon, set_button_icon
try:
    import minecraft_launcher_lib
    HAS_LIB = True
except ImportError:
    HAS_LIB = False
_DETACHED_WORKERS = set()
_CLEANUP_REGISTERED = False

class _LoaderCard(QFrame):
    clicked = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('LoaderCard')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('\n            QFrame#LoaderCard {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.14);\n                border-radius: 8px;\n            }\n            QFrame#LoaderCard:hover {\n                background: rgba(28,31,40,0.72);\n                border-color: rgba(122,160,255,0.42);\n            }\n            QFrame#LoaderCard QLabel {\n                background: transparent;\n                border: none;\n            }\n        ')

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _shutdown_detached_workers():
    workers = list(_DETACHED_WORKERS)
    for worker in workers:
        try:
            if worker.isRunning() and not worker.wait(1200):
                worker.terminate()
                worker.wait(300)
        except RuntimeError:
            continue
    _DETACHED_WORKERS.clear()

def _register_cleanup():
    global _CLEANUP_REGISTERED
    if _CLEANUP_REGISTERED:
        return
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(_shutdown_detached_workers)
        _CLEANUP_REGISTERED = True

def _version_tuple(text) -> tuple:
    match = re.search('(\\d+)\\.(\\d+)(?:\\.(\\d+))?', str(text or ''))
    if not match:
        return ()
    return tuple(int(part) for part in match.groups() if part)

class LoadVersionsWorker(QThread):
    finished = Signal(dict)
    partial = Signal(dict)
    def __init__(self, version_id, load_optional=True, load_optifabric=True):
        super().__init__()
        self.version_id = version_id
        self.load_optional = bool(load_optional)
        self.load_optifabric = bool(load_optifabric)
        self._cancelled = False
        self._optifine_objects = {}
        self.stability = {
            'forge': {}, 'neoforge': {}, 'fabric': {},
            'fabric_api': {}, 'optifine': {}, 'optifabric': {},
        }

    def cancel(self):
        self._cancelled = True

    def _fetch_json(self, url, timeout=8):
        resp = requests.get(url, headers={'User-Agent': 'PMGL-Launcher/1.1', 'Accept': 'application/json'}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _get_forge(self):
        version = _version_tuple(self.version_id)
        if version and version < (1, 5, 2):
            return []
        for base in ('https://bmclapi2.bangbang93.com', 'https://bmclapi.bangbang93.com'):
            try:
                data = self._fetch_json(f'{base}/forge/minecraft/{self.version_id}')
                if isinstance(data, list):
                    versions = []
                    for item in data:
                        value = item.get('version') if isinstance(item, dict) else None
                        if value:
                            value = str(value)
                            versions.append(value)
                            self.stability['forge'][value] = True
                    if versions:
                        return versions[:30]
            except Exception:
                continue
        # 官方 promotions 只提供 latest/recommended，作为镜像不可用时的兜底
        try:
            data = self._fetch_json(
                'https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json'
            )
            promos = data.get('promos') if isinstance(data, dict) else {}
            versions = []
            for key, value in (promos or {}).items():
                if not str(key).startswith(f'{self.version_id}-') or not value:
                    continue
                value = str(value)
                if value not in versions:
                    versions.append(value)
            if versions:
                for value in versions:
                    self.stability['forge'][value] = True
                return versions
        except Exception:
            pass
        return []

    def _get_neoforge(self):
        version = _version_tuple(self.version_id)
        if version and version < (1, 20):
            return []
        try:
            data = self._fetch_json(f'https://bmclapi2.bangbang93.com/neoforge/list/{self.version_id}')
            if isinstance(data, list):
                versions = []
                for item in data:
                    value = item.get('version') if isinstance(item, dict) else None
                    if value:
                        value = str(value)
                        versions.append(value)
                        self.stability['neoforge'][value] = True
                return versions[:30]
        except Exception:
            pass
        return []

    def _get_optifine(self):
        version = _version_tuple(self.version_id)
        if version and version < (1, 7, 2):
            return []
        try:
            from core.optifine import OptiFineManager
            versions = OptiFineManager().get_versions(self.version_id)
            self._optifine_objects = {v.id: v for v in versions}
            for item in versions:
                self.stability['optifine'][str(item.id)] = not item.is_preview
            return [v.id for v in versions]
        except Exception:
            return []

    def _get_fabric(self):
        version = _version_tuple(self.version_id)
        if version and version < (1, 14):
            return []
        for url in (
            f'https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/loader/{self.version_id}',
            f'https://openbmclapi.bangbang93.com/fabric-meta/v2/versions/loader/{self.version_id}',
            f'https://meta.fabricmc.net/v2/versions/loader/{self.version_id}',
        ):
            try:
                data = self._fetch_json(url, timeout=6)
                if not isinstance(data, list):
                    continue
                stable = []
                all_versions = []
                seen = set()
                for item in data:
                    loader_data = item.get('loader') if isinstance(item, dict) else None
                    if not isinstance(loader_data, dict):
                        continue
                    value = loader_data.get('version')
                    if not value:
                        continue
                    value = str(value)
                    self.stability['fabric'][value] = bool(loader_data.get('stable'))
                    if value in seen:
                        continue
                    seen.add(value)
                    all_versions.append(value)
                    if loader_data.get('stable'):
                        stable.append(value)
                if all_versions:
                    merged = stable + [v for v in all_versions if v not in stable]
                    return merged[:30]
            except Exception:
                pass
        return []

    def _get_fabric_api(self):
        version = _version_tuple(self.version_id)
        if version and version < (1, 14):
            return []
        params = {
            'game_versions': json.dumps([self.version_id]),
            'loaders': json.dumps(['fabric']),
            'limit': 8,
        }
        for url in (
            'https://api.modrinth.com/v2/project/P7dR8mSH/version',
            'https://bmclapi2.bangbang93.com/modrinth/v2/project/P7dR8mSH/version',
        ):
            try:
                resp = requests.get(
                    url, params=params,
                    headers={'User-Agent': 'PMGL-Launcher/1.1'}, timeout=8,
                )
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    continue
                versions = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    value = item.get('version_number')
                    if not value:
                        continue
                    value = str(value)
                    versions.append(value)
                    kind = str(item.get('version_type') or '').lower()
                    self.stability['fabric_api'][value] = (kind == 'release')
                if versions:
                    return versions
            except Exception:
                pass
        return []

    def _get_optifabric(self):
        version = _version_tuple(self.version_id)
        if version and version < (1, 14):
            return []
        try:
            from core.optifabric import fetch_versions
            return fetch_versions(self.version_id)
        except Exception:
            return []

    def run(self):
        result = {'forge': [], 'neoforge': [], 'fabric': [], 'fabric_api': [], 'optifine': [], 'optifabric': []}
        jobs = {'forge': self._get_forge, 'neoforge': self._get_neoforge, 'fabric': self._get_fabric, 'fabric_api': self._get_fabric_api}
        if self.load_optional:
            jobs['optifine'] = self._get_optifine
        if self.load_optifabric:
            jobs['optifabric'] = self._get_optifabric
        executor = ThreadPoolExecutor(max_workers=5)
        futures = {key: executor.submit(fn) for key, fn in jobs.items()}
        pending = set(futures.values())
        completed = set()
        while pending and not self._cancelled:
            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for fut in done:
                for key, f in futures.items():
                    if f is fut:
                        try:
                            result[key] = fut.result() or []
                        except Exception:
                            result[key] = []
                        completed.add(key)
                        break
            if done and not self._cancelled:
                self.partial.emit({key: result[key] for key in completed})
        executor.shutdown(wait=False, cancel_futures=True)
        result['optifine_objects'] = dict(self._optifine_objects)
        if not self._cancelled:
            self.finished.emit(result)


class VersionDetailPage(QWidget):
    back_requested = Signal()
    download_requested = Signal(str, dict)
    def __init__(self, version_id, parent=None):
        super().__init__(parent)
        self.setObjectName('VersionDetailPage')
        self.version_id = version_id
        self.config = None
        self._selections = {'forge': None, 'neoforge': None, 'fabric': None, 'fabric_api': None, 'optifine': None, 'optifabric': None}
        self._loader_versions = {'forge': [], 'neoforge': [], 'fabric': [], 'fabric_api': [], 'optifine': [], 'optifabric': []}
        self._loaded_loader_ids = set()
        self._loader_cancel_buttons = {}
        self._optifabric_files = {}
        self._optifabric_offered = False
        self._loader_buttons = {}
        self._loader_status_labels = {}
        self._warning_tip_label = None
        self._optifine_display = {}
        self._optifine_objects = {}
        self._user_edited_top = False
        self.fabric_api_visible = False
        self._loading = True
        self._init_ui()
        self._sync_top_name()
        try:
            self.content_scroll.hide()
        except RuntimeError:
            pass
        self.loading_overlay.start_auto('正在加载加载器版本', 92)
        from PySide6.QtCore import QTimer
        self._loading_watchdog = QTimer(self)
        self._loading_watchdog.setSingleShot(True)
        self._loading_watchdog.setInterval(12000)
        self._loading_watchdog.timeout.connect(self._on_loading_watchdog)
        self._loading_watchdog.start()
        self._load_worker = LoadVersionsWorker(version_id)
        self._load_worker.finished.connect(self._on_versions_loaded)
        self._load_worker.partial.connect(self._on_partial_versions_loaded)
        self._load_worker.start()
        _register_cleanup()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet('VersionDetailPage { background-color: #222233; }')
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet('\n            QScrollArea { background: transparent; border: none; }\n            QScrollBar:vertical { background: #2a2a2a; width: 12px; border-radius: 6px; }\n            QScrollBar::handle:vertical { background: #555; border-radius: 6px; min-height: 20px; }\n            QScrollBar::handle:vertical:hover { background: #777; }\n            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }\n        ')
        content = QWidget()
        content.setStyleSheet('background-color: rgba(23,23,23,0.7);')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 30)
        layout.setSpacing(12)
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setSpacing(8)
        back_btn = BackButton()
        back_btn.clicked.connect((lambda: self.back_requested.emit()))
        self.top_name_input = QLineEdit()
        self.top_name_input.setStyleSheet('\n            QLineEdit {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.14);\n                border-radius: 8px;\n                color: #f0f0f0;\n                padding: 6px 10px;\n                font-size: 14px;\n            }\n            QLineEdit:focus {\n                border-color: rgba(122,160,255,0.55);\n            }\n        ')
        self.top_name_input.textEdited.connect(self._on_top_name_edit)
        self.name_error_label = QLabel('')
        self.name_error_label.setStyleSheet('color:#ff6b6b; font-size:12px; background:transparent; padding-left:2px;')
        self.name_error_label.hide()
        name_box = QVBoxLayout()
        name_box.setSpacing(3)
        name_box.addWidget(self.top_name_input)
        name_box.addWidget(self.name_error_label)
        self.top_name_input.textChanged.connect(self._validate_top_name)
        top_bar_layout.addWidget(back_btn)
        top_bar_layout.addLayout(name_box, 1)
        layout.addLayout(top_bar_layout)
        self._warning_tip_label = QLabel('如果不安装 Fabric API，大多数 Mod 都会无法使用!')
        self._warning_tip_label.setStyleSheet('\n            QLabel {\n                background: rgba(255,180,180,0.25);\n                color: #ff4444;\n                padding: 8px 12px;\n                border-left: 4px solid #ff3333;\n                font-size: 13px;\n                font-weight: bold;\n            }\n        ')
        self._warning_tip_label.setVisible(False)
        layout.addWidget(self._warning_tip_label)
        layout.addSpacerItem(QSpacerItem(0, 10, QSizePolicy.Minimum, QSizePolicy.Fixed))
        self.compat_warning_label = QLabel('OptiFine 与一部分模组的兼容性不佳，请谨慎安装。')
        self.compat_warning_label.setWordWrap(True)
        self.compat_warning_label.setStyleSheet('QLabel { background: rgba(214,143,24,0.88); color:#2b1a00; border-left:3px solid #f0a030; padding:7px 10px; border-radius:4px; font-size:12px; }')
        self.compat_warning_label.hide()
        layout.addWidget(self.compat_warning_label)
        self.loader_section = QWidget()
        self._loader_section_layout = QVBoxLayout(self.loader_section)
        self._loader_section_layout.setContentsMargins(0, 0, 0, 0)
        self._loader_section_layout.setSpacing(8)
        self.loader_widgets = {}
        self._loader_version_lists = {}
        self._loader_expanded = {}
        self._loader_animations = {}
        self._loader_arrows = {}
        self._loader_cards = {}
        loaders = [('Forge', 'forge'), ('NeoForge', 'neoforge'), ('Fabric', 'fabric'), ('OptiFine', 'optifine')]
        for display_name, loader_id in loaders:
            container = self._create_loader_row(display_name, loader_id, has_versions=True)
            if not self._loader_supported(loader_id):
                container.hide()
            self._loader_section_layout.addWidget(container)
            self.loader_widgets[loader_id] = container
        optifabric_container = self._create_loader_row('OptiFabric', 'optifabric', has_versions=True)
        optifabric_container.hide()
        self._loader_section_layout.addWidget(optifabric_container)
        self.loader_widgets['optifabric'] = optifabric_container
        layout.addWidget(self.loader_section)
        self.fabric_api_container = self._create_loader_row('Fabric API', 'fabric_api', has_versions=True)
        self.fabric_api_container.hide()
        self.fabric_api_visible = False
        layout.addSpacerItem(QSpacerItem(0, 30, QSizePolicy.Minimum, QSizePolicy.Fixed))
        self.download_btn = QPushButton('开始下载')
        self.download_btn.setMinimumHeight(50)
        self.download_btn.setFont(ui_font(12, QFont.DemiBold))
        self.download_btn.setStyleSheet('\n            QPushButton { background: #0078d4; color: white; border: none; border-radius: 8px; }\n            QPushButton:hover { background: #106ebe; }\n            QPushButton:pressed { background: #005a9e; }\n            QPushButton:disabled { background:#333; color:#777; }\n        ')
        self.download_btn.clicked.connect(self._on_download_clicked)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        btn_layout.addWidget(self.download_btn)
        btn_layout.addStretch(1)
        layout.addLayout(btn_layout)
        layout.addStretch(1)
        scroll.setWidget(content)
        main_layout.addWidget(scroll)
        self.content_scroll = scroll
        self.loading_overlay = LoadingOverlay(self, block_input=False)
        self.loading_overlay.attach_to(self)
        self.loading_overlay.hidden.connect(self._on_loading_hidden)

    @staticmethod
    def _loader_icon_path(loader_id: str) -> Path:
        mapping = {'forge': 'forge.png', 'neoforge': 'neo_forge.png', 'fabric': 'fabric.png', 'fabric_api': 'fabric.png', 'optifine': 'optifine.png', 'optifabric': 'OptiFabric.png'}
        filename = mapping.get(str(loader_id).lower(), '')
        if not filename:
            return Path()
        return Path(__file__).resolve().parent.parent / 'imgs' / filename

    def _create_loader_row(self, display_name, loader_id, has_versions=True):
        container = _LoaderCard()
        self._loader_cards[loader_id] = container
        outer = QVBoxLayout(container)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(10)
        name_label = QLabel(display_name)
        name_label.setFont(ui_font(14))
        name_label.setStyleSheet('color: #f0f0f0; background:transparent;')
        name_label.setFixedWidth(110)
        name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(name_label)
        status_label = QLabel('' if has_versions else '无可用版本')
        status_label.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 13px; background:transparent;')
        status_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(status_label, 1)
        self._loader_status_labels[loader_id] = status_label
        cancel_btn = QPushButton()
        cancel_btn.setFixedSize(22, 22)
        cancel_btn.setIconSize(QSize(14, 14))
        set_button_icon(cancel_btn, 'close', 14, '#ff7b72')
        cancel_btn.setToolTip('取消选择')
        cancel_btn.setStyleSheet('QPushButton { border:none; background:transparent; border-radius:5px; }QPushButton:hover { background:rgba(255,90,90,0.16); }')
        cancel_btn.clicked.connect((lambda _checked=False, lid=loader_id: self._cancel_loader_selection(lid)))
        cancel_btn.hide()
        row.addWidget(cancel_btn)
        self._loader_cancel_buttons[loader_id] = cancel_btn
        arrow = QLabel()
        arrow.setFixedSize(18, 18)
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setPixmap(painted_icon('chevron_right', 16, '#aab4c0').pixmap(16, 16))
        arrow.setStyleSheet('background:transparent;')
        arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(arrow)
        self._loader_arrows[loader_id] = arrow
        if not has_versions:
            arrow.hide()
            container.setCursor(Qt.ArrowCursor)
        btn = QPushButton()
        btn.hide()
        self._loader_buttons[loader_id] = btn
        container.clicked.connect((lambda lid=loader_id: self._on_loader_card_clicked(lid)))
        outer.addLayout(row)
        version_list = QListWidget()
        version_list.setStyleSheet('\n            QListWidget { background:rgba(8,10,16,0.45);\n                          border:1px solid rgba(255,255,255,0.10);\n                          border-radius:6px; outline:none; }\n            QListWidget::item { padding:6px 10px; color:rgba(255,255,255,0.85);\n                                border-bottom:1px solid rgba(255,255,255,0.04); }\n            QListWidget::item:selected { background:rgba(0,120,212,0.45); color:white; }\n            QListWidget::item:hover { background:rgba(255,255,255,0.06); }\n        ')
        version_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        version_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        version_list.setIconSize(QSize(16, 16))
        version_list.setFixedHeight(150)
        version_list.hide()
        version_list.itemClicked.connect((lambda item, lid=loader_id: self._on_loader_version_selected(lid, item)))
        version_list.itemDoubleClicked.connect((lambda item, lid=loader_id: self._on_loader_version_selected(lid, item)))
        outer.addWidget(version_list)
        self._loader_version_lists[loader_id] = version_list
        self._loader_expanded[loader_id] = False
        version_list.setMinimumHeight(0)
        version_list.setMaximumHeight(0)
        return container

    def _on_loader_card_clicked(self, loader_id):
        versions = self._loader_versions.get(loader_id, [])
        if not versions:
            return
        self._on_loader_button_clicked(loader_id)

    def _cancel_loader_selection(self, loader_id):
        if self._selections.get(loader_id) is not None:
            self._deselect_loader(loader_id)

    def _populate_loader_versions(self, loader_id, versions):
        body = self._loader_version_lists.get(loader_id)
        if body is None:
            return
        body.clear()
        icon_path = self._loader_icon_path(loader_id)
        icon = QIcon(str(icon_path)) if icon_path.is_file() else QIcon()
        if loader_id == 'optifabric':
            self._optifabric_files = {}
        for version in versions or []:
            if loader_id == 'optifabric' and isinstance(version, dict):
                label = str(version.get('version') or version.get('filename') or '')
                value = str(version.get('version') or version.get('filename') or '')
                self._optifabric_files[value] = version
            else:
                label = self._optifine_display.get(version, version) if loader_id == 'optifine' else str(version)
                value = version
            item = QListWidgetItem(icon, label)
            item.setData(Qt.UserRole, value)
            body.addItem(item)
        if versions:
            target = min(190, max(52, 30 * len(versions) + 8))
            body._target_height = target
            if self._loader_animations.get(loader_id) is None:
                if self._loader_expanded.get(loader_id):
                    body.setMinimumHeight(target)
                    body.setMaximumHeight(target)
                    body.show()
                else:
                    body.setMinimumHeight(0)
                    body.setMaximumHeight(0)
                    body.hide()
            arrow = self._loader_arrows.get(loader_id)
            if arrow is not None:
                arrow.show()
                self._set_loader_arrow_state(
                    loader_id, self._loader_expanded.get(loader_id, False)
                )
            card = self._loader_cards.get(loader_id)
            if card is not None:
                card.setCursor(Qt.PointingHandCursor)
                return
            return
        self._stop_loader_animation(loader_id)
        self._loader_expanded[loader_id] = False
        body.setMinimumHeight(0)
        body.setMaximumHeight(0)
        body.hide()
        arrow = self._loader_arrows.get(loader_id)
        if arrow is not None:
            arrow.hide()
        card = self._loader_cards.get(loader_id)
        if card is not None:
            card.setCursor(Qt.ArrowCursor)

    def _stop_loader_animation(self, loader_id):
        animation = self._loader_animations.pop(loader_id, None)
        if animation is not None:
            try:
                animation.stop()
                animation.deleteLater()
            except RuntimeError:
                pass

    def _set_loader_expanded(self, loader_id, expanded, animate=True):
        body = self._loader_version_lists.get(loader_id)
        if body is None:
            return
        self._stop_loader_animation(loader_id)
        self._loader_expanded[loader_id] = bool(expanded)
        self._set_loader_arrow_state(loader_id, bool(expanded))
        target = int(getattr(body, '_target_height', 0) or 0)
        if target <= 0:
            target = min(190, max(52, body.sizeHint().height() or 52))
            body._target_height = target
        if not animate or not self.isVisible():
            if expanded:
                body.setMinimumHeight(target)
                body.setMaximumHeight(target)
                body.show()
            else:
                body.setMinimumHeight(0)
                body.setMaximumHeight(0)
                body.hide()
            return
        if expanded:
            body.setMinimumHeight(0)
            body.setMaximumHeight(0)
            body.show()
            animation = QPropertyAnimation(body, b'maximumHeight', self)
            animation.setDuration(160)
            animation.setStartValue(0)
            animation.setEndValue(target)
            animation.setEasingCurve(QEasingCurve.OutCubic)

            def finish_expand():
                if self._loader_animations.get(loader_id) is not animation:
                    return
                final_target = int(getattr(body, '_target_height', target) or target)
                body.setMinimumHeight(final_target)
                body.setMaximumHeight(final_target)
                self._loader_animations.pop(loader_id, None)

            animation.finished.connect(finish_expand)
            self._loader_animations[loader_id] = animation
            animation.start()
            body.setFocus()
            return
        start = max(1, body.height() or target)
        body.setMinimumHeight(0)
        body.setMaximumHeight(start)
        body.show()
        animation = QPropertyAnimation(body, b'maximumHeight', self)
        animation.setDuration(160)
        animation.setStartValue(start)
        animation.setEndValue(0)
        animation.setEasingCurve(QEasingCurve.OutCubic)

        def finish_collapse():
            if self._loader_animations.get(loader_id) is not animation:
                return
            body.setMinimumHeight(0)
            body.setMaximumHeight(0)
            body.hide()
            self._loader_animations.pop(loader_id, None)

        animation.finished.connect(finish_collapse)
        self._loader_animations[loader_id] = animation
        animation.start()

    def _set_loader_arrow_state(self, loader_id, expanded: bool):
        arrow = self._loader_arrows.get(loader_id)
        if arrow is None:
            return
        arrow.setPixmap(painted_icon('chevron_up' if expanded else 'chevron_right', 16, '#aab4c0').pixmap(16, 16))

    def _toggle_loader_versions(self, loader_id):
        body = self._loader_version_lists.get(loader_id)
        if body is None or body.count() == 0:
            return
        self._set_loader_expanded(
            loader_id, not self._loader_expanded.get(loader_id, False)
        )

    def _on_loader_version_selected(self, loader_id, item):
        version = item.data(Qt.UserRole) if item is not None else None
        if version:
            self._select_loader(loader_id, version)

    def _on_loading_hidden(self):
        try:
            self.content_scroll.show()
        except RuntimeError:
            pass

    def _needed_loader_keys(self):
        keys = ('forge', 'neoforge', 'fabric', 'fabric_api', 'optifine')
        return [key for key in keys if self._loader_supported(key)]

    def _update_loading_progress(self):
        if not self.loading_overlay.is_loading():
            return
        needed = self._needed_loader_keys()
        if not needed:
            self.loading_overlay.finish()
            return
        loaded = len(set(needed) & self._loaded_loader_ids)
        self.loading_overlay.set_progress(int(loaded * 100 / len(needed)))
        if loaded >= len(needed):
            self._finish_loading()

    def _finish_loading(self):
        if hasattr(self, '_loading_watchdog'):
            try:
                self._loading_watchdog.stop()
            except RuntimeError:
                pass
        if not self.loading_overlay.is_loading():
            return
        needed = self._needed_loader_keys()
        if needed and all(not self._loader_versions.get(key) for key in needed):
            reason = (
                '未连接到互联网，无法获取加载器版本'
                if not network_available()
                else '加载器版本获取失败，请稍后重试'
            )
            try:
                self.content_scroll.show()
            except RuntimeError:
                pass
            self.loading_overlay.fail(reason)
        else:
            self.loading_overlay.finish()

    def _on_loading_watchdog(self):
        '''加载超过 30 秒时放行页面，避免一直卡在加载中。'''

        if not self.loading_overlay.is_loading():
            return
        self._loading = False
        self._loaded_loader_ids.update(self._needed_loader_keys())
        self._update_loader_ui()
        self._refresh_all_compatible_tips()
        self._finish_loading()

    def _on_partial_versions_loaded(self, versions_dict):
        if not versions_dict:
            return
        self._loader_versions.update(versions_dict)
        self._loaded_loader_ids.update(versions_dict.keys())
        self._update_loader_ui()
        self._refresh_all_compatible_tips()
        self._update_loading_progress()

    def _on_versions_loaded(self, versions_dict):
        self._loading = False
        objects = versions_dict.pop('optifine_objects', {}) or {}
        self._loader_versions = versions_dict
        self._loaded_loader_ids.update(versions_dict.keys())
        self._optifine_objects = objects
        self._optifine_display = {vid: ver.display() for vid, ver in objects.items()}
        self._update_loader_ui()
        self._validate_top_name()
        self._refresh_all_compatible_tips()
        self._sync_top_name()
        self._finish_loading()

    def _update_loader_ui(self):
        for loader_id, versions in self._loader_versions.items():
            label = self._loader_status_labels.get(loader_id)
            btn = self._loader_buttons.get(loader_id)
            if not label or not btn:
                continue
            if loader_id not in self._loaded_loader_ids:
                label.setText('' if self.loading_overlay.is_loading() else '加载中...')
                label.setStyleSheet('color: rgba(255,255,255,0.45); font-size: 13px;')
                self._set_loader_expanded(loader_id, False, animate=False)
                arrow = self._loader_arrows.get(loader_id)
                if arrow is not None:
                    arrow.hide()
                card = self._loader_cards.get(loader_id)
                if card is not None:
                    card.setCursor(Qt.ArrowCursor)
                continue
            self._populate_loader_versions(loader_id, versions)
            frame = self.loader_widgets.get(loader_id)
            if self._selections.get(loader_id) is not None:
                if frame is not None:
                    frame.setVisible(True)
                continue
            if not versions:
                if not network_available():
                    if frame is not None:
                        frame.setVisible(True)
                    label.setText('网络获取失败，请检查网络')
                    label.setStyleSheet('color: #ff8a8a; font-size: 13px;')
                    btn.setText('失败')
                    btn.setEnabled(False)
                    btn.setStyleSheet('background:#3a3a3a;color:#888;border-radius:4px;')
                else:
                    # 没有匹配当前游戏版本的加载器时直接隐藏空行
                    if frame is not None:
                        frame.setVisible(False)
                continue
            if frame is not None:
                frame.setVisible(True)
            label.setText('可以添加')
            label.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 13px;')
            btn.setText('添加')
            btn.setEnabled(True)
            btn.setStyleSheet('background:#0078d4;color:white;border-radius:4px;padding:4px 8px;')

    def _refresh_all_compatible_tips(self):
        fabric_selected = self._selections['fabric'] is not None
        fabric_api_selected = self._selections['fabric_api'] is not None
        forge_label = self._loader_status_labels['forge']
        forge_btn = self._loader_buttons['forge']
        if fabric_selected and self._selections['forge'] is None:
            forge_label.setText('与 Fabric 不兼容')
            forge_label.setStyleSheet('color: #aaaaaa; font-size: 13px;')
            forge_btn.setText('不兼容')
            forge_btn.setEnabled(False)
            forge_btn.setStyleSheet('background:#333;color:#777;border-radius:4px;')
        elif self._selections['forge'] is not None:
            forge_label.setText(self._selections['forge'])
            forge_label.setStyleSheet('color:#4ade80;font-size:13px;')
            forge_btn.setText('✕')
            forge_btn.setEnabled(True)
            forge_btn.setStyleSheet('background:#6c2a2a;color:white;')
        elif self._loader_versions['forge']:
            forge_label.setText('可以添加')
            forge_label.setStyleSheet('color:rgba(255,255,255,0.6);font-size:13px;')
            forge_btn.setText('添加')
            forge_btn.setEnabled(True)
            forge_btn.setStyleSheet('background:#0078d4;color:white;')
        else:
            forge_label.setText('无可用版本')
            forge_label.setStyleSheet('color:rgba(255,255,255,0.4);font-size:13px;')
            forge_btn.setText('无')
            forge_btn.setEnabled(False)
            forge_btn.setStyleSheet('background:#3a3a3a;color:#888;')
        neo_label = self._loader_status_labels['neoforge']
        neo_btn = self._loader_buttons['neoforge']
        if fabric_selected and self._selections['neoforge'] is None:
            neo_label.setText('与 Fabric 不兼容')
            neo_label.setStyleSheet('color: #aaaaaa; font-size: 13px;')
            neo_btn.setText('不兼容')
            neo_btn.setEnabled(False)
            neo_btn.setStyleSheet('background:#333;color:#777;border-radius:4px;')
        elif self._selections['neoforge'] is not None:
            neo_label.setText(self._selections['neoforge'])
            neo_label.setStyleSheet('color:#4ade80;font-size:13px;')
            neo_btn.setText('✕')
            neo_btn.setEnabled(True)
            neo_btn.setStyleSheet('background:#6c2a2a;color:white;')
        elif self._loader_versions['neoforge']:
            neo_label.setText('可以添加')
            neo_label.setStyleSheet('color:rgba(255,255,255,0.6);font-size:13px;')
            neo_btn.setText('添加')
            neo_btn.setEnabled(True)
            neo_btn.setStyleSheet('background:#0078d4;color:white;')
        else:
            neo_label.setText('无可用版本')
            neo_label.setStyleSheet('color:rgba(255,255,255,0.4);font-size:13px;')
            neo_btn.setText('无')
            neo_btn.setEnabled(False)
            neo_btn.setStyleSheet('background:#3a3a3a;color:#888;')
        opti_label = self._loader_status_labels['optifine']
        opti_btn = self._loader_buttons['optifine']
        if self._selections['optifine'] is not None:
            opti_label.setText(self._selections['optifine'])
            opti_label.setStyleSheet('color:#4ade80;font-size:13px;')
            opti_btn.setText('✕')
            opti_btn.setEnabled(True)
            opti_btn.setStyleSheet('background:#6c2a2a;color:white;')
        elif self._loader_versions['optifine']:
            opti_label.setText('可以添加')
            opti_label.setStyleSheet('color:rgba(255,255,255,0.6);font-size:13px;')
            opti_btn.setText('添加')
            opti_btn.setEnabled(True)
            opti_btn.setStyleSheet('background:#0078d4;color:white;')
        else:
            opti_label.setText('无可用版本')
            opti_label.setStyleSheet('color:rgba(255,255,255,0.4);font-size:13px;')
            opti_btn.setText('无')
            opti_btn.setEnabled(False)
            opti_btn.setStyleSheet('background:#3a3a3a;color:#888;')
        fab_label = self._loader_status_labels['fabric']
        fab_btn = self._loader_buttons['fabric']
        if self._selections['fabric'] is not None:
            fab_label.setText(self._selections['fabric'])
            fab_label.setStyleSheet('color:#4ade80;font-size:13px;')
            fab_btn.setText('✕')
            fab_btn.setEnabled(True)
            fab_btn.setStyleSheet('background:#6c2a2a;color:white;')
        elif self._loader_versions['fabric']:
            fab_label.setText('可以添加')
            fab_label.setStyleSheet('color:rgba(255,255,255,0.6);font-size:13px;')
            fab_btn.setText('添加')
            fab_btn.setEnabled(True)
            fab_btn.setStyleSheet('background:#0078d4;color:white;')
        else:
            fab_label.setText('无可用版本')
            fab_label.setStyleSheet('color:rgba(255,255,255,0.4);font-size:13px;')
            fab_btn.setText('无')
            fab_btn.setEnabled(False)
            fab_btn.setStyleSheet('background:#3a3a3a;color:#888;')
        api_label = self._loader_status_labels['fabric_api']
        api_btn = self._loader_buttons['fabric_api']
        if self._selections['fabric_api'] is not None:
            api_label.setText(self._selections['fabric_api'])
            api_label.setStyleSheet('color:#4ade80;font-size:13px;')
            api_btn.setText('✕')
            api_btn.setEnabled(True)
            api_btn.setStyleSheet('background:#6c2a2a;color:white;')
        elif self._loader_versions['fabric_api']:
            api_label.setText('可以添加')
            api_label.setStyleSheet('color:rgba(255,255,255,0.6);font-size:13px;')
            api_btn.setText('添加')
            api_btn.setEnabled(True)
            api_btn.setStyleSheet('background:#0078d4;color:white;')
        else:
            api_label.setText('无可用版本')
            api_label.setStyleSheet('color:rgba(255,255,255,0.4);font-size:13px;')
            api_btn.setText('无')
            api_btn.setEnabled(False)
            api_btn.setStyleSheet('background:#3a3a3a;color:#888;')
        fabric_selected = self._selections['fabric'] is not None
        opti_selected = self._selections['optifine'] is not None
        neo_selected = self._selections['neoforge'] is not None
        show_optifabric = fabric_selected and opti_selected and self._loader_supported('optifabric')
        if neo_selected and opti_selected:
            self.compat_warning_label.setText('OptiFine 与 NeoForge 不兼容，请取消其中一个加载器。')
            self.compat_warning_label.setStyleSheet(
                'QLabel { background: rgba(214,60,60,0.92); color:#fff; border-left:3px solid #ff4444;'
                ' padding:7px 10px; border-radius:4px; font-size:12px; font-weight:600; }'
            )
            self.compat_warning_label.show()
        elif fabric_selected and opti_selected:
            self.compat_warning_label.setText('OptiFine 与 Fabric 需要 OptiFabric 兼容层，请同时安装 OptiFabric。')
            self.compat_warning_label.setStyleSheet(
                'QLabel { background: rgba(214,143,24,0.88); color:#2b1a00; border-left:3px solid #f0a030;'
                ' padding:7px 10px; border-radius:4px; font-size:12px; }'
            )
            self.compat_warning_label.show()
        else:
            self.compat_warning_label.hide()
        optifabric_card = self.loader_widgets.get('optifabric')
        if optifabric_card is not None:
            optifabric_card.setVisible(show_optifabric)
            if not show_optifabric:
                self._selections['optifabric'] = None
                self._optifabric_offered = False
        if show_optifabric:
            versions = self._loader_versions.get('optifabric', [])
            loaded = 'optifabric' in self._loaded_loader_ids
            if versions and not self._optifabric_offered:
                first = versions[0]
                value = str(first.get('version') or first.get('filename') or '') if isinstance(first, dict) else str(first)
                if value:
                    self._selections['optifabric'] = value
                    self._optifabric_offered = True
            current = self._selections.get('optifabric')
            label = self._loader_status_labels.get('optifabric')
            if label is not None:
                if current:
                    label.setText(str(current))
                    label.setStyleSheet('color:#4ade80;font-size:13px;')
                elif not loaded:
                    label.setText('' if self.loading_overlay.is_loading() else '加载中...')
                    label.setStyleSheet('color:rgba(255,255,255,0.45);font-size:13px;')
                elif versions:
                    label.setText('可以添加')
                    label.setStyleSheet('color:rgba(255,255,255,0.6);font-size:13px;')
                else:
                    label.setText('无可用版本')
                    label.setStyleSheet('color:rgba(255,255,255,0.4);font-size:13px;')
        self._warning_tip_label.setVisible(fabric_selected and not fabric_api_selected)
        for loader_id in getattr(self, '_loader_cards', {}):
            self._sync_loader_arrow(loader_id)
        for loader_id, label in self._loader_status_labels.items():
            if loader_id in self._loaded_loader_ids:
                continue
            label.setText('' if self.loading_overlay.is_loading() else '加载中...')
            label.setStyleSheet('color: rgba(255,255,255,0.45); font-size: 13px;')
            arrow = self._loader_arrows.get(loader_id)
            if arrow is not None:
                arrow.hide()
            if loader_id in self._loader_version_lists:
                self._set_loader_expanded(loader_id, False, animate=False)
            card = self._loader_cards.get(loader_id)
            if card is not None:
                card.setCursor(Qt.ArrowCursor)

    def _sync_loader_arrow(self, loader_id):
        arrow = getattr(self, '_loader_arrows', {}).get(loader_id)
        card = getattr(self, '_loader_cards', {}).get(loader_id)
        body = getattr(self, '_loader_version_lists', {}).get(loader_id)
        cancel_btn = getattr(self, '_loader_cancel_buttons', {}).get(loader_id)
        versions = self._loader_versions.get(loader_id, [])
        selected = self._selections.get(loader_id) is not None
        if arrow is not None:
            if versions:
                arrow.show()
                self._set_loader_arrow_state(
                    loader_id, self._loader_expanded.get(loader_id, False)
                )
            else:
                arrow.hide()
        if cancel_btn is not None:
            cancel_btn.setVisible(selected)
        if card is not None:
            card.setCursor(Qt.PointingHandCursor if versions else Qt.ArrowCursor)

    def _on_loader_button_clicked(self, loader_id):
        versions = self._loader_versions.get(loader_id, [])
        if not versions:
            if self._loading:
                QMessageBox.information(self, '提示', f'{loader_id.capitalize()} 版本列表仍在加载，请稍后再试。')
                return
            QMessageBox.information(self, '提示', f'暂无 {loader_id} 可用版本')
            return
        main_load_ids = ['forge', 'neoforge', 'fabric']
        if loader_id not in main_load_ids:
            self._toggle_loader_versions(loader_id)
            return
        for other in main_load_ids:
            if other == loader_id:
                continue
            if self._selections[other] is not None:
                QMessageBox.warning(self, '不兼容提示', f'{loader_id.capitalize()} 与 {other.capitalize()} 不能同时安装，请先取消另一加载器！')
                return
        self._toggle_loader_versions(loader_id)

    def _select_loader(self, loader_id, version):
        self._selections[loader_id] = version
        status_label = self._loader_status_labels[loader_id]
        status_label.setText(version)
        status_label.setStyleSheet('color: #4ade80; font-size: 13px;')
        btn = self._loader_buttons[loader_id]
        btn.setText('✕')
        btn.setStyleSheet('background:#6c2a2a;color:white;border-radius:4px;')
        body = self._loader_version_lists.get(loader_id)
        if body is not None:
            for row in range(body.count()):
                item = body.item(row)
                if item.data(Qt.UserRole) == version:
                    body.setCurrentRow(row)
                    break
            self._set_loader_expanded(loader_id, False)
        if loader_id == 'fabric':
            self._show_fabric_api()
            if self._selections['fabric_api'] is None and self._loader_versions['fabric_api']:
                latest_fabric_api_ver = self._loader_versions['fabric_api'][0]
                self._selections['fabric_api'] = latest_fabric_api_ver
                api_label = self._loader_status_labels['fabric_api']
                api_label.setText(latest_fabric_api_ver)
                api_label.setStyleSheet('color: #4ade80; font-size: 13px;')
                api_btn = self._loader_buttons['fabric_api']
                api_btn.setText('✕')
                api_btn.setStyleSheet('background:#6c2a2a;color:white;border-radius:4px;')
        self._refresh_all_compatible_tips()
        self._sync_top_name()

    def _deselect_loader(self, loader_id):
        self._selections[loader_id] = None
        if loader_id in self._loader_version_lists:
            self._set_loader_expanded(loader_id, False)
        self._refresh_all_compatible_tips()
        self._sync_top_name()
        if loader_id == 'fabric':
            self._hide_fabric_api()
            self._selections['fabric_api'] = None
            self._refresh_all_compatible_tips()

    def _show_fabric_api(self):
        if self.fabric_api_visible:
            return
        parent_layout = getattr(self, '_loader_section_layout', None)
        if parent_layout is None:
            return
        fabric_widget = self.loader_widgets.get('fabric')
        insert_idx = parent_layout.indexOf(fabric_widget)
        if insert_idx >= 0:
            parent_layout.insertWidget(insert_idx + 1, self.fabric_api_container)
            self.fabric_api_container.show()
            self.fabric_api_visible = True

    def _hide_fabric_api(self):
        if not self.fabric_api_visible:
            return
        parent_layout = getattr(self, '_loader_section_layout', None)
        if parent_layout is None:
            return
        parent_layout.removeWidget(self.fabric_api_container)
        self.fabric_api_container.hide()
        if 'fabric_api' in self._loader_version_lists:
            self._set_loader_expanded('fabric_api', False, animate=False)
        self.fabric_api_visible = False
        label = self._loader_status_labels['fabric_api']
        label.setText('可以添加' if self._loader_versions['fabric_api'] else '无')
        label.setStyleSheet('color:rgba(255,255,255,0.6);font-size:13px;')
        api_btn = self._loader_buttons['fabric_api']
        api_btn.setText('添加' if self._loader_versions['fabric_api'] else '无')
        api_btn.setEnabled(bool(self._loader_versions['fabric_api']))
        if self._loader_versions['fabric_api']:
            api_btn.setStyleSheet('background:#0078d4;color:white;')
            return
        api_btn.setStyleSheet('background:#3a3a3a;color:#888;')

    def _suggest_unique_name(self, base: str) -> str:
        from pathlib import Path
        from core.config import ConfigManager
        from core.version import INCOMPLETE_MARKER
        versions_dir = Path(ConfigManager().get_mc_dir()) / 'versions'
        target = versions_dir / base
        if not target.exists() or (target / INCOMPLETE_MARKER).exists():
            return base
        index = 2
        while True:
            candidate = versions_dir / f'{base} ({index})'
            if not candidate.exists() or (candidate / INCOMPLETE_MARKER).exists():
                return candidate.name
            index += 1

    def _sync_top_name(self):
        if self._user_edited_top:
            return
        base_text = self.version_id
        fabric_ver = self._selections['fabric']
        forge_ver = self._selections['forge']
        neo_ver = self._selections['neoforge']
        opti_ver = self._selections['optifine']
        if fabric_ver and opti_ver:
            auto_text = f'{base_text}-Fabric {fabric_ver}-OptiFine {opti_ver}'
        elif fabric_ver:
            auto_text = f'{base_text}-Fabric {fabric_ver}'
        elif forge_ver:
            auto_text = f'{base_text}-Forge {forge_ver}'
        elif neo_ver:
            auto_text = f'{base_text}-NeoForge {neo_ver}'
        elif opti_ver:
            auto_text = f'{base_text}-OptiFine {opti_ver}'
        else:
            auto_text = base_text
        self.top_name_input.setText(self._suggest_unique_name(auto_text))

    @staticmethod
    def _parse_version_tuple(text) -> tuple:
        return version_tuple(text)

    def _loader_supported(self, loader_id: str) -> bool:
        return loader_supported(self.version_id, loader_id)

    def _on_top_name_edit(self):
        self._user_edited_top = True

    _WINDOWS_RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL', *{f'COM{i}' for i in range(1, 10)}, *{f'LPT{i}' for i in range(1, 10)}}
    def _version_name_error(self, name: str) -> str:
        '''返回版本名称错误提示；空字符串表示名称可用。'''

        from pathlib import Path
        from core.config import ConfigManager
        from core.version import INCOMPLETE_MARKER
        name = (name or '').strip()
        if not name:
            return '版本名称不能为空！'
        if '*' in name:
            return '版本名称禁止包含 * 符号！'
        if re.search('[<>:"/\\\\|?*\\x00-\\x1f]', name):
            return '版本名称不能包含 < > : " / \\ | ? * 等字符！'
        if name.endswith((' ', '.')):
            return '版本名称不能以空格或点结尾！'
        if name.split('.', 1)[0].strip().upper() in self._WINDOWS_RESERVED_NAMES:
            return '该名称为系统保留名称，请更换！'
        versions_dir = Path(ConfigManager().get_mc_dir()) / 'versions'
        try:
            entries = list(versions_dir.iterdir())
        except OSError:
            entries = []
        lowered = name.casefold()
        for item in entries:
            if item.name.casefold() != lowered:
                continue
            if item.is_dir() and (item / INCOMPLETE_MARKER).exists():
                continue
            return '不可与现有文件夹重名！'
        return ''

    def _validate_top_name(self, *_args) -> bool:
        if not hasattr(self, 'name_error_label'):
            return True
        error = self._version_name_error(self.top_name_input.text())
        if error:
            self.name_error_label.setText(error)
            self.name_error_label.show()
            self.top_name_input.setStyleSheet('\n                QLineEdit {\n                    background: rgba(18,20,26,0.58);\n                    border: 1px solid #e05555;\n                    border-radius: 8px;\n                    color: #f0f0f0;\n                    padding: 6px 10px;\n                    font-size: 14px;\n                }\n            ')
            if hasattr(self, 'download_btn'):
                self.download_btn.setEnabled(False)
            return False
        self.name_error_label.hide()
        self.top_name_input.setStyleSheet('\n            QLineEdit {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.14);\n                border-radius: 8px;\n                color: #f0f0f0;\n                padding: 6px 10px;\n                font-size: 14px;\n            }\n            QLineEdit:focus {\n                border-color: rgba(122,160,255,0.55);\n            }\n        ')
        if hasattr(self, 'download_btn') and not self._loading:
            self.download_btn.setEnabled(True)
        return True

    def _on_download_clicked(self):
        if not self._validate_top_name():
            self.top_name_input.setFocus()
            return
        custom_name = self.top_name_input.text().strip()
        if self._selections['optifine'] and self._selections['neoforge']:
            QMessageBox.warning(self, '不兼容提示', 'OptiFine 不能与 NeoForge 同时安装。\n请先取消其中一个加载器。')
            return
        selected_all = {k: v for k, v in self._selections.items() if v is not None}
        if selected_all.get('optifabric'):
            value = str(selected_all['optifabric'])
            file_info = self._optifabric_files.get(value)
            if file_info:
                selected_all['optifabric'] = file_info
            else:
                selected_all.pop('optifabric', None)
        self.download_requested.emit(custom_name, selected_all)

    def shutdown(self):
        try:
            self.loading_overlay.stop()
        except RuntimeError:
            pass
        if hasattr(self, '_loading_watchdog'):
            try:
                self._loading_watchdog.stop()
            except RuntimeError:
                pass
        worker = self._load_worker
        self._load_worker = None
        if worker is not None:
            try:
                if worker.isRunning():
                    worker.cancel()
                # 不在 UI 线程等待网络线程，避免点击“开始下载”时窗口未响应
                _DETACHED_WORKERS.add(worker)
                worker.setParent(None)
                worker.finished.connect((lambda w=worker: _DETACHED_WORKERS.discard(w)))
                worker.finished.connect(worker.deleteLater)
            except RuntimeError:
                pass

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


