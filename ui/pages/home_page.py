'''
首页 - 启动页面，包含版本选择子页、启动/终止按钮、游戏设置
修复：终止游戏进程稳定，避免崩溃；添加 refresh_versions 方法
'''

import os
import json
import re
import sys
import logging
from pathlib import Path
from PySide6.QtCore import (Qt, Signal, QTimer, QThread, QSize)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QFrame, QListWidget, QListWidgetItem, QGroupBox, QScrollArea, QMessageBox, QStackedWidget, QSizePolicy, QApplication)
from PySide6.QtGui import (QFont, QColor, QPalette, QPixmap, QIcon)
from core.app_font import ui_font
from ui.widgets.painted_icons import painted_icon, set_button_icon
from ui.widgets.launch_progress_button import LaunchProgressButton
from ui.version_meta import version_description, version_icon, display_name as version_display_name
from ui.widgets.back_button import BackButton
from core.config import ConfigManager
from core.download_manager import DownloadManager
from core.logging_setup import LOG_DIR
from core.version import (VersionManager, VersionDeleteWorker, cleanup_deleted_version)
from core.launch import (LaunchManager, LaunchWorker, set_process_priority)
from ui.pages.mod_manager_dialog import ModManagerPage
logger = logging.getLogger('HomePage')
_DETACHED_LAUNCH_WORKERS = set()

class VersionDisplay(QFrame):
    '''首页当前版本展示：图标和文字分开布局，避免图标被行高裁切。'''

    ICON_HEIGHT = 28
    ICON_MAX_WIDTH = 72
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('VersionDisplay')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(50)
        self._light = False
        self._text_color = 'rgba(255,255,255,0.9)'
        self.setStyleSheet('\n            QFrame#VersionDisplay {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.24);\n                border-radius: 9px;\n            }\n            QFrame#VersionDisplay QLabel {\n                background: transparent;\n                border: none;\n            }\n        ')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(10)
        layout.addStretch(1)
        self.icon_label = QLabel()
        self.icon_label.setFixedHeight(self.ICON_HEIGHT)
        self.icon_label.setMinimumWidth(self.ICON_HEIGHT)
        self.icon_label.setMaximumWidth(self.ICON_MAX_WIDTH)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.hide()
        self.text_label = QLabel()
        self.text_label.setFont(ui_font(16))
        self.text_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.text_label.setProperty('_theme_ignore', True)
        layout.addWidget(self.icon_label, 0, Qt.AlignVCenter)
        layout.addWidget(self.text_label, 0, Qt.AlignVCenter)
        layout.addStretch(1)

    def _apply_text_color(self):
        color = self._text_color
        if self._light:
            color = re.sub(
                r'rgba\(\s*255\s*,\s*255\s*,\s*255',
                'rgba(27,27,27', color, flags=re.I,
            )
            color = re.sub(
                r'#(?:fff|ffffff)\b', '#1b1b1b', color, flags=re.I
            )
            color = re.sub(r'\bwhite\b', '#1b1b1b', color, flags=re.I)
        self.text_label.setStyleSheet(
            f'color:{color}; background:transparent; border:none;'
        )

    def set_light(self, light):
        self._light = bool(light)
        self._apply_text_color()

    def set_content(self, text: str, icon_path=None, color: str='rgba(255,255,255,0.9)'):
        self.text_label.setText(str(text))
        self._text_color = str(color)
        self._light = (
            QApplication.palette().color(QPalette.Window).lightness() > 128
        )
        self._apply_text_color()
        if icon_path is not None:
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                from ui.widgets.settings_widgets import hidpi_pixmap
                pixmap = hidpi_pixmap(pixmap, self.ICON_MAX_WIDTH, self.ICON_HEIGHT)
                self.icon_label.setFixedWidth(max(self.ICON_HEIGHT, pixmap.width()))
                self.icon_label.setFixedHeight(self.ICON_HEIGHT)
                self.icon_label.setPixmap(pixmap)
                self.icon_label.show()
                return
        self.icon_label.clear()
        self.icon_label.hide()


class VersionItemWidget(QWidget):
    version_selected = Signal(str)
    TYPE_LABELS = {'release': '正式版', 'snapshot': '快照版', 'old_beta': '旧版 Beta', 'old_alpha': '旧版 Alpha'}
    LOADER_LABELS = {'fabric': 'Fabric', 'forge': 'Forge', 'neoforge': 'NeoForge', 'quilt': 'Quilt', 'optifine': 'OptiFine'}
    def __init__(self, version_id, version_type, is_favorited, loader='', parent=None):
        super().__init__(parent)
        self.version_id = version_id
        self.version_type = version_type
        self.loader = loader
        self.is_favorited = is_favorited
        self.home_page = None
        p = self.parent()
        while p and not isinstance(p, HomePage):
            p = p.parent()
        self.home_page = p
        self._init_ui()

    def _version_description(self):
        if self.home_page is not None:
            try:
                mc_dir = self.home_page.config.get_mc_dir()
                return version_description(
                    mc_dir, self.version_id, self.home_page.config,
                    self.version_type, self.loader,
                )
            except Exception:
                pass
        return f'{self.TYPE_LABELS.get(str(self.version_type or "").lower(), "未知")} {self.version_id}'

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
        icon_path = None
        if self.home_page is not None:
            try:
                icon_path = self.home_page._version_icon_path({'id': self.version_id, 'type': self.version_type, 'loader': self.loader})
            except Exception:
                icon_path = None
        if icon_path:
            icon_label = QLabel()
            pix = QPixmap(str(icon_path))
            if not pix.isNull():
                from ui.widgets.settings_widgets import hidpi_pixmap
                icon_label.setPixmap(hidpi_pixmap(pix, 28, 28))
                icon_label.setFixedSize(30, 30)
                icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                layout.addWidget(icon_label, 0, Qt.AlignVCenter)
        name_box = QVBoxLayout()
        name_box.setContentsMargins(0, 0, 0, 0)
        name_box.setSpacing(1)
        nickname = self.version_id
        if self.home_page is not None:
            try:
                nickname = version_display_name(self.home_page.config, self.version_id)
            except Exception:
                nickname = self.version_id
        self.name_label = QLabel(nickname)
        self.name_label.setStyleSheet('color: rgba(255,255,255,0.92); font-weight: 600;')
        self.description_label = QLabel(self._version_description())
        self.description_label.setStyleSheet('color: rgba(255,255,255,0.48); font-size: 11px;')
        name_box.addWidget(self.name_label)
        name_box.addWidget(self.description_label)
        layout.addLayout(name_box, 1)
        self.fav_btn = QPushButton()
        self.fav_btn.setFixedSize(28, 28)
        self.fav_btn.setIconSize(QSize(18, 18))
        self.fav_btn.setStyleSheet('QPushButton { border:none; background:transparent; border-radius:5px; }QPushButton:hover { background:rgba(255,255,255,0.10); }')
        self._update_fav_icon()
        self.fav_btn.clicked.connect(self._on_fav_clicked)
        layout.addWidget(self.fav_btn)
        self.del_btn = QPushButton()
        self.del_btn.setFixedSize(28, 28)
        self.del_btn.setIconSize(QSize(18, 18))
        set_button_icon(self.del_btn, 'trash', 18, '#9aa4b5')
        self.del_btn.setToolTip('删除版本')
        self.del_btn.setStyleSheet('QPushButton { border:none; background:transparent; border-radius:5px; }QPushButton:hover { background:rgba(255,90,90,0.16); }')
        self.del_btn.clicked.connect(self._on_del_clicked)
        layout.addWidget(self.del_btn)
        self.set_btn = QPushButton()
        self.set_btn.setFixedSize(28, 28)
        self.set_btn.setIconSize(QSize(18, 18))
        set_button_icon(self.set_btn, 'gear', 18, '#9aa4b5')
        self.set_btn.setToolTip('游戏设置')
        self.set_btn.setStyleSheet('QPushButton { border:none; background:transparent; border-radius:5px; }QPushButton:hover { background:rgba(126,231,135,0.14); }')
        self.set_btn.clicked.connect(self._on_set_clicked)
        layout.addWidget(self.set_btn)

    def _update_fav_icon(self):
        if self.is_favorited:
            set_button_icon(self.fav_btn, 'heart_filled', 18, '#ff5a6a')
            self.fav_btn.setToolTip('取消收藏')
            return
        set_button_icon(self.fav_btn, 'heart', 18, '#9aa4b5')
        self.fav_btn.setToolTip('收藏版本')

    def _on_fav_clicked(self):
        if not self.home_page:
            return
        self.is_favorited = not self.is_favorited
        self._update_fav_icon()
        favorites = self.home_page.config.get('favorited_versions', [])
        if self.is_favorited:
            if self.version_id not in favorites:
                favorites.append(self.version_id)
        elif self.version_id in favorites:
            favorites.remove(self.version_id)
        self.home_page.config.set('favorited_versions', favorites)
        self.home_page.config.save()
        self.home_page._refresh_selector_list()

    def _on_del_clicked(self):
        if not self.home_page:
            return
        if self.home_page._delete_worker is not None and self.home_page._delete_worker.isRunning():
            self.home_page.status_label.setText('请等待当前版本删除完成')
            return
        reply = QMessageBox.question(self.home_page, '确认删除', f'确定要删除版本 {self.version_id} 及其所有文件吗？\n此操作不可撤销！', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        mc_dir = self.home_page.config.get_mc_dir()
        version_dir = Path(mc_dir) / 'versions' / self.version_id
        self.del_btn.setEnabled(False)
        self.del_btn.setToolTip('正在删除...')
        self.home_page.start_version_delete(self.version_id, version_dir)

    def _on_set_clicked(self):
        if self.home_page:
            self.home_page.open_game_settings.emit(self.version_id)

    def mousePressEvent(self, event):
        if self.fav_btn.geometry().contains(event.pos()) or self.del_btn.geometry().contains(event.pos()) or self.set_btn.geometry().contains(event.pos()):
            super().mousePressEvent(event)
            return
        if self.home_page:
            self.home_page._on_version_clicked(self.version_id)
            return
        self.version_selected.emit(self.version_id)


class LaunchPrepareWorker(QThread):
    '''在后台校验/修复版本并准备启动命令，避免界面卡死。'''

    success = Signal(object, str)
    error = Signal(str)
    status = Signal(str)
    progress = Signal(int)
    def __init__(self, launch_mgr, version_id: str, force_java_path: str=''):
        super().__init__()
        self.launch_mgr = launch_mgr
        self.version_id = version_id
        self.force_java_path = force_java_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if bool(self.launch_mgr.config.get('memory_optimize_before_launch', False)):
                self.status.emit('正在优化内存...')
                try:
                    import gc
                    import sys
                    gc.collect()
                    if sys.platform == 'win32':
                        import ctypes
                        handle = ctypes.windll.kernel32.GetCurrentProcess()
                        ctypes.windll.psapi.EmptyWorkingSet(handle)
                except Exception:
                    pass
            from core.version import VersionManager
            from core.repair import RepairEngine, RepairCancelled
            mc_dir = self.launch_mgr.config.get_mc_dir()
            settings = self.launch_mgr.get_version_settings(self.version_id) or {}
            skip_verify = settings.get('disable_file_verification')
            if skip_verify is None or skip_verify == '':
                skip_verify = self.launch_mgr.config.get('disable_file_verification', False)
            if not bool(skip_verify):
                manager = VersionManager(mc_dir)
                status = manager.validate_version(self.version_id)
                if status.get('fatal'):
                    self.error.emit('版本文件损坏，无法启动：\n' + '\n'.join(status.get('issues') or ['未知错误']) + '\n\n请在版本选择列表中删除后重新安装。')
                    return
                self.status.emit('正在检查缺失文件（快速启动）...')
                engine = RepairEngine(self.version_id, mc_dir, status=self.status.emit, progress=self.progress.emit, cancel=(lambda: self._cancelled), fast=True, missing_only=True)
                result = engine.repair()
                if self._cancelled:
                    return
                if result.failed:
                    self.error.emit('启动前自动修复失败：\n' + '\n'.join(result.failed_items or ['未知文件']))
                    return
            else:
                self.status.emit('正在检查缺失的游戏文件...')
                engine = RepairEngine(self.version_id, mc_dir, status=self.status.emit, progress=self.progress.emit, cancel=(lambda: self._cancelled), fast=True, missing_only=True)
                result = engine.repair()
                if self._cancelled:
                    return
                if result.failed:
                    self.error.emit('补全缺失文件失败：\n' + '\n'.join(result.failed_items or ['未知文件']))
                    return
            self.status.emit('正在生成启动命令...')
            command, err = self.launch_mgr.build_launch_command(self.version_id, prompt_java=False, force_java_path=self.force_java_path)
            if command:
                self.progress.emit(100)
                self.success.emit(command, self.version_id)
            return
        except RepairCancelled:
            if not self._cancelled:
                self.error.emit('启动前修复已取消')
                return
            return
        except Exception as exc:
            self.error.emit(str(exc))
            return


class JavaPreflightWorker(QThread):
    '''后台检查 Java 环境，避免点击启动按钮时界面卡顿。'''

    done = Signal(dict)
    def __init__(self, launch_mgr, version_id: str):
        super().__init__()
        self.launch_mgr = launch_mgr
        self.version_id = version_id

    def run(self):
        try:
            state = self.launch_mgr.preflight_java(self.version_id)
        except Exception:
            state = {'status': 'ok'}
        self.done.emit(state or {'status': 'ok'})


class HomePage(QWidget):
    request_switch_page = Signal(int)
    game_dir_changed = Signal(str)
    request_offline_login = Signal()
    open_game_settings = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('HomePage')
        self.config = ConfigManager()
        self.version_mgr = VersionManager()
        self.launch_mgr = LaunchManager()
        self._launch_worker = None
        self._instances = []
        self._instance_info = {}
        self._running_rows = {}
        self._starting_worker = None
        self._prepare_worker = None
        self._preflight_worker = None
        self._prepare_task_id = ''
        self._prepare_done = False
        self._launch_preparing = False
        self._prepare_cancelled = False
        self._preflight_cancelled = False
        self._delete_worker = None
        self._launcher_hidden_by_game = False
        self._is_running = False
        self.selected_version_id = None
        self._selected_version_fatal = False
        self._mod_manager_page = None
        self._init_ui()
        QTimer.singleShot(100, self._refresh_all)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.stacked = QStackedWidget()
        self.stacked.setStyleSheet('background: transparent;')
        home_widget = QWidget()
        home_layout = QVBoxLayout(home_widget)
        home_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet('\n            QScrollArea { background: transparent; border: none; }\n            QScrollBar:vertical { background: #2a2a2a; width: 12px; border-radius: 6px; }\n            QScrollBar::handle:vertical { background: #555; border-radius: 6px; min-height: 20px; }\n            QScrollBar::handle:vertical:hover { background: #777; }\n            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }\n        ')
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(60, 50, 60, 40)
        layout.setSpacing(30)
        title = QLabel('欢迎使用SCL启动器！')
        title.setFont(ui_font(32, QFont.DemiBold))
        title.setStyleSheet('color: #f0f0f0;')
        layout.addWidget(title)
        subtitle = QLabel('Spark Craft Launcher')
        subtitle.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 14px;')
        layout.addWidget(subtitle)
        layout.addStretch(1)
        card = QGroupBox()
        card.setStyleSheet('\n            QGroupBox {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.22);\n                border-radius: 16px;\n                margin-top: 0px;\n                padding-top: 10px;\n            }\n        ')
        card.setMinimumHeight(200)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(30, 20, 30, 20)
        card_layout.setSpacing(16)
        label_row = QHBoxLayout()
        version_label = QLabel('当前版本')
        version_label.setStyleSheet('color: rgba(255,255,255,0.8); font-size: 14px; font-weight: 600;')
        label_row.addWidget(version_label)
        label_row.addStretch(1)
        card_layout.addLayout(label_row)
        self.version_display = VersionDisplay()
        card_layout.addWidget(self.version_display)
        self.status_label = QLabel('就绪')
        self.status_label.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 13px;')
        card_layout.addWidget(self.status_label)
        self.login_notice_label = QLabel('')
        self.login_notice_label.setWordWrap(True)
        self.login_notice_label.setStyleSheet(
            'color:#f0b35a; font-size:12px; font-weight:600;'
            ' background:rgba(240,160,48,0.14);'
            ' border-left:3px solid #f0a030; border-radius:5px;'
            ' padding:6px 10px;'
        )
        self.login_notice_label.hide()
        card_layout.addWidget(self.login_notice_label)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.select_version_btn = QPushButton('选择版本')
        self.select_version_btn.setMinimumHeight(50)
        self.select_version_btn.setFixedWidth(120)
        self.select_version_btn.setFont(ui_font(12))
        self.select_version_btn.setStyleSheet('\n            QPushButton {\n                background: #3a3a3a;\n                color: white;\n                border: none;\n                border-radius: 8px;\n            }\n            QPushButton:hover { background: #4a4a4a; }\n            QPushButton:pressed { background: #2a2a2a; }\n            QPushButton:disabled { background: #2a2a2a; color: #666; }\n        ')
        self.select_version_btn.clicked.connect(self._show_version_selector)
        self.action_btn = LaunchProgressButton('启动游戏')
        self.action_btn.setMinimumHeight(50)
        self.action_btn.setFont(ui_font(14, QFont.DemiBold))
        self.action_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.action_btn.setProperty('_theme_ignore', True)
        self.action_btn.clicked.connect(self._on_action_clicked)
        self.settings_btn = QPushButton('游戏设置')
        self.settings_btn.setMinimumHeight(50)
        self.settings_btn.setFixedWidth(120)
        self.settings_btn.setFont(ui_font(12))
        self.settings_btn.setStyleSheet('\n            QPushButton {\n                background: #3a3a3a;\n                color: white;\n                border: none;\n                border-radius: 8px;\n            }\n            QPushButton:hover { background: #4a4a4a; }\n            QPushButton:pressed { background: #2a2a2a; }\n            QPushButton:disabled { background: #2a2a2a; color: #666; }\n        ')
        self.settings_btn.clicked.connect(self._on_settings_clicked_from_home)
        self.mod_manager_btn = QPushButton('管理模组')
        self.mod_manager_btn.setMinimumHeight(50)
        self.mod_manager_btn.setFixedWidth(120)
        self.mod_manager_btn.setFont(ui_font(12))
        self.mod_manager_btn.setStyleSheet(self.settings_btn.styleSheet())
        self.mod_manager_btn.clicked.connect(self._open_mod_manager_home)
        self.mod_manager_btn.hide()
        btn_row.addWidget(self.select_version_btn)
        btn_row.addWidget(self.action_btn)
        btn_row.addWidget(self.mod_manager_btn)
        btn_row.addWidget(self.settings_btn)
        card_layout.addLayout(btn_row)
        layout.addWidget(card)
        self.running_group = QGroupBox('运行中的游戏 (0)')
        self.running_group.setStyleSheet(self._group_style())
        self.running_layout = QVBoxLayout(self.running_group)
        self.running_layout.setContentsMargins(18, 12, 18, 12)
        self.running_layout.setSpacing(8)
        self.running_group.hide()
        layout.addWidget(self.running_group)
        layout.addStretch(1)
        scroll.setWidget(content)
        home_layout.addWidget(scroll)
        self.stacked.addWidget(home_widget)
        selector_widget = QWidget()
        selector_layout = QVBoxLayout(selector_widget)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(40, 0, 40, 0)
        back_btn = BackButton()
        back_btn.clicked.connect(self._go_back_home)
        title_sel = QLabel('选择版本')
        title_sel.setFont(ui_font(20, QFont.DemiBold))
        title_sel.setStyleSheet('color: #f0f0f0;')
        top_bar.addWidget(back_btn)
        top_bar.addWidget(title_sel)
        top_bar.addStretch(1)
        dir_label = QLabel('游戏目录')
        dir_label.setStyleSheet('color: rgba(255,255,255,0.55); font-size: 12px;')
        top_bar.addWidget(dir_label)
        self.selector_dir_combo = QComboBox()
        self.selector_dir_combo.setMinimumWidth(280)
        self.selector_dir_combo.setMinimumHeight(32)
        self.selector_dir_combo.setStyleSheet(
            'QComboBox { background: rgba(18,20,26,0.72); color: #f0f0f0;'
            ' border: 1px solid rgba(255,255,255,0.18); border-radius: 7px;'
            ' padding: 4px 10px; }'
            'QComboBox::drop-down { border: none; }'
            'QComboBox QAbstractItemView { background: #24262c; color: #f0f0f0;'
            ' border: 1px solid rgba(255,255,255,0.14); }'
        )
        self.selector_dir_combo.activated.connect(self._on_selector_dir_changed)
        self.selector_dir_combo.setContextMenuPolicy(Qt.CustomContextMenu)
        self.selector_dir_combo.customContextMenuRequested.connect(self._show_selector_dir_menu)
        top_bar.addWidget(self.selector_dir_combo)
        self.selector_dir_add_btn = QPushButton('添加')
        self.selector_dir_add_btn.setFixedSize(56, 32)
        self.selector_dir_add_btn.setStyleSheet(
            'QPushButton { background: #0078d4; color: white; border: none;'
            ' border-radius: 6px; padding: 6px 10px; }'
            'QPushButton:hover { background: #1084e0; }'
        )
        self.selector_dir_add_btn.clicked.connect(self._on_selector_dir_add)
        top_bar.addWidget(self.selector_dir_add_btn)
        self.selector_dir_del_btn = QPushButton('删除')
        self.selector_dir_del_btn.setFixedSize(56, 32)
        self.selector_dir_del_btn.setStyleSheet(
            'QPushButton { background: #3a3a3a; color: white; border: none;'
            ' border-radius: 6px; padding: 6px 10px; }'
            'QPushButton:hover { background: #4a4a4a; }'
        )
        self.selector_dir_del_btn.clicked.connect(self._on_selector_dir_remove)
        top_bar.addWidget(self.selector_dir_del_btn)
        self._refreshing_dirs = False
        selector_layout.addLayout(top_bar)
        scroll_sel = QScrollArea()
        scroll_sel.setWidgetResizable(True)
        scroll_sel.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_sel.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_sel.setStyleSheet('\n            QScrollArea { background: transparent; border: none; }\n            QScrollBar:vertical { background: rgba(255,255,255,0.06); width: 13px; margin: 2px; border-radius: 6px; }\n            QScrollBar::handle:vertical { background: rgba(165,195,255,0.62); border-radius: 5px; min-height: 32px; }\n            QScrollBar::handle:vertical:hover { background: rgba(190,215,255,0.9); }\n            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }\n            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }\n        ')
        content_sel = QWidget()
        self.selector_layout = QVBoxLayout(content_sel)
        self.selector_layout.setContentsMargins(40, 30, 40, 30)
        self.selector_layout.setSpacing(20)
        self.fav_group = QGroupBox('收藏的版本列表 (0)')
        self.fav_group.setStyleSheet(self._group_style())
        fav_layout = QVBoxLayout(self.fav_group)
        self.fav_list = QListWidget()
        self.fav_list.setStyleSheet(self._list_style())
        self.fav_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fav_layout.addWidget(self.fav_list)
        self.selector_layout.addWidget(self.fav_group)
        self.mod_group = QGroupBox('可安装Mod版本 (0)')
        self.mod_group.setStyleSheet(self._group_style())
        mod_layout = QVBoxLayout(self.mod_group)
        self.mod_list = QListWidget()
        self.mod_list.setStyleSheet(self._list_style())
        self.mod_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        mod_layout.addWidget(self.mod_list)
        self.selector_layout.addWidget(self.mod_group)
        self.vanilla_group = QGroupBox('原版 (0)')
        self.vanilla_group.setStyleSheet(self._group_style())
        vanilla_layout = QVBoxLayout(self.vanilla_group)
        self.vanilla_list = QListWidget()
        self.vanilla_list.setStyleSheet(self._list_style())
        self.vanilla_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        vanilla_layout.addWidget(self.vanilla_list)
        self.selector_layout.addWidget(self.vanilla_group)
        self.selector_empty = QWidget()
        self.selector_empty.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        empty_layout = QVBoxLayout(self.selector_empty)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.setSpacing(16)
        empty_layout.addStretch(1)
        empty_label = QLabel('当前没有版本')
        empty_label.setAlignment(Qt.AlignCenter)
        empty_label.setFont(ui_font(20, QFont.DemiBold))
        empty_label.setStyleSheet('color: rgba(255,255,255,0.72);')
        empty_layout.addWidget(empty_label, 0, Qt.AlignHCenter)
        self.selector_download_btn = QPushButton('前往下载')
        self.selector_download_btn.setMinimumHeight(44)
        self.selector_download_btn.setMinimumWidth(180)
        self.selector_download_btn.setCursor(Qt.PointingHandCursor)
        self.selector_download_btn.setStyleSheet(
            'QPushButton { background:#0078d4; color:white; border:none;'
            ' border-radius:9px; padding:9px 34px; font-size:14px; font-weight:600; }'
            'QPushButton:hover { background:#1084e0; }'
            'QPushButton:pressed { background:#006cbd; }'
        )
        self.selector_download_btn.clicked.connect(
            (lambda: self.request_switch_page.emit(1))
        )
        empty_layout.addWidget(self.selector_download_btn, 0, Qt.AlignHCenter)
        empty_layout.addStretch(1)
        self.selector_empty.hide()
        self.selector_layout.addWidget(self.selector_empty, 1)
        self._selector_empty_index = self.selector_layout.indexOf(self.selector_empty)
        self._selector_bottom_stretch = self.selector_layout.count()
        self.selector_layout.addStretch(1)
        scroll_sel.setWidget(content_sel)
        selector_layout.addWidget(scroll_sel)
        self.stacked.addWidget(selector_widget)
        main_layout.addWidget(self.stacked)

    def _group_style(self):
        return '\n            QGroupBox {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.22);\n                border-radius: 12px;\n                margin-top: 0px;\n                padding-top: 8px;\n            }\n        '

    def _list_style(self):
        return '\n            QListWidget {\n                background: rgba(10,12,18,0.45);\n                border: 1px solid rgba(255,255,255,0.16);\n                border-radius: 10px;\n            }\n            QListWidget::item {\n                padding: 0px;\n                border-bottom: 1px solid rgba(255,255,255,0.05);\n            }\n            QListWidget::item:selected {\n                background: rgba(122,160,255,0.2);\n                border-left: 3px solid #7aa0ff;\n            }\n        '

    def _categorize_versions(self, versions):
        mod = []
        vanilla = []
        for v in versions:
            vid = v.get('id')
            if not vid:
                continue
            settings = (self.config.get('version_settings', {}) or {}).get(vid, {}) or {}
            category = str(settings.get('category') or 'auto').lower()
            if category == 'vanilla':
                is_mod = False
            elif category in ('modded', 'modpack'):
                is_mod = True
            else:
                is_mod = bool(v.get('loader')) or self._is_modded_version(vid)
            if is_mod:
                mod.append(v)
            else:
                vanilla.append(v)
        return mod, vanilla

    def _refresh_all(self):
        versions = self.version_mgr.get_installed_versions()
        if not versions:
            self._update_home_display(None)
            self._update_selector_list([])
            return
        if len(versions) == 1:
            single_id = versions[0].get('id')
            self.config.set('selected_version', single_id)
            self.config.save()
            self.selected_version_id = single_id
        else:
            saved = self.config.get('selected_version')
            if saved:
                if any((v.get('id') == saved for v in versions)):
                    self.selected_version_id = saved
                else:
                    self.selected_version_id = None
                    self.config.set('selected_version', None)
                    self.config.save()
        self._update_home_display(versions)
        self._update_selector_list(versions)

    def start_version_delete(self, version_id, version_dir):
        '''后台删除版本目录，删除期间保持界面可响应。'''

        if self._delete_worker is not None and self._delete_worker.isRunning():
            return
        self.status_label.setText(f'正在删除版本 {version_id} ...')
        self.action_btn.setEnabled(False)
        self.action_btn.setText('正在删除...')
        worker = VersionDeleteWorker(version_id, version_dir, self)
        self._delete_worker = worker
        worker.done.connect(self._on_version_delete_done)
        worker.failed.connect(self._on_version_delete_failed)
        worker.finished.connect((lambda w=worker: self._on_version_delete_finished(w)))
        worker.start()

    def _on_version_delete_done(self, version_id):
        cleanup_deleted_version(self.config, version_id)
        if self.selected_version_id == version_id:
            self.selected_version_id = None
        self._refresh_all()
        self._restore_action_button()
        QMessageBox.information(self, '成功', f'版本 {version_id} 已删除')

    def _on_version_delete_failed(self, version_id, error):
        self.status_label.setText(f'删除版本 {version_id} 失败')
        self._refresh_all()
        self._restore_action_button()
        QMessageBox.critical(self, '删除失败', f'无法删除版本 {version_id}：{error}\n\n如果游戏正在运行，请先退出游戏后重试。')

    def _on_version_delete_finished(self, worker):
        if self._delete_worker is worker:
            self._delete_worker = None
        worker.deleteLater()

    _LOADER_ICON_FILES = {'forge': 'forge.png', 'fabric': 'fabric.png', 'neoforge': 'neo_forge.png', 'optifine': 'optifine.png'}
    def _version_icon_path(self, version):
        vid = str((version or {}).get('id') or '')
        if not vid:
            return None
        try:
            return version_icon(
                self.version_mgr.mc_dir,
                vid,
                self.config,
                str((version or {}).get('type') or ''),
                str((version or {}).get('loader') or ''),
            )
        except Exception:
            return None

    def _set_version_display(self, text, version=None, color='rgba(255,255,255,0.9)'):
        icon_path = self._version_icon_path(version) if version else None
        self.version_display.set_content(text, icon_path, color)

    def _version_login_notice(self, version_id):
        settings = (self.config.get('version_settings', {}) or {}).get(version_id, {}) or {}
        mode = str(settings.get('login_mode') or 'global').lower()
        if mode in ('', 'global'):
            return ''
        if mode == 'microsoft':
            label = '正版登录'
        elif mode == 'offline':
            label = '离线登录'
        elif mode == 'thirdparty':
            account = self.config.get('thirdparty_account') or {}
            provider = str(account.get('server_name') or '').strip()
            if provider.endswith('登录'):
                provider = provider[:-2].strip()
            if not provider:
                api = str(account.get('api') or '').strip()
                try:
                    from urllib.parse import urlparse
                    host = urlparse(api).hostname or ''
                    parts = [part for part in host.split('.') if part]
                    provider = parts[-2] if len(parts) >= 2 else host
                except Exception:
                    provider = ''
            label = f'{provider} 登录' if provider else '第三方登录'
        else:
            label = f'{mode} 登录'
        return f'当前登录方式已被修改为：{label}'

    def _update_home_display(self, versions):
        # 启动准备/启动中时，其他刷新不能把进度按钮重置成“启动游戏”
        if self._launch_preparing or self._starting_worker is not None:
            return
        if not versions:
            self._set_version_display('当前没有版本', color='rgba(255,255,255,0.6)')
            self.status_label.setText('当前没有版本，可前往下载')
            # 没有下载任何游戏时：只保留「选择版本」与蓝色「下载游戏」
            self.select_version_btn.show()
            self.select_version_btn.setEnabled(True)
            self.settings_btn.hide()
            self.mod_manager_btn.hide()
            self.action_btn.set_idle('下载游戏')
            self.action_btn.setToolTip('')
            self.login_notice_label.hide()
            return
        self.select_version_btn.setEnabled(True)
        self.select_version_btn.show()
        self.settings_btn.show()
        if self.selected_version_id:
            v = next((x for x in versions if x.get('id') == self.selected_version_id), None)
            if v:
                vid = v.get('id')
                try:
                    info = self.version_mgr.validate_version(vid) or {}
                except Exception:
                    info = {}
                issues = info.get('issues') or []
                fatal = bool(info.get('fatal'))
                self._selected_version_fatal = fatal
                display = version_display_name(self.config, vid)
                notice = self._version_login_notice(vid)
                self.login_notice_label.setText(f'⚠ {notice}' if notice else '')
                self.login_notice_label.setVisible(bool(notice))
                self.settings_btn.setEnabled(True)
                self.mod_manager_btn.hide()
                if fatal:
                    self._set_version_display(f'{display}   [错误：无法启动]', v)
                    self.status_label.setText('错误版本无法启动，请删除后重新安装')
                    self.action_btn.set_disabled_state('错误版本无法启动')
                    self.action_btn.setToolTip('；'.join(issues))
                    return
                self._selected_version_fatal = False
                self._set_version_display(display, v)
                hint = '（启动前会自动修复）' if issues else ''
                self.status_label.setText(f'当前版本：{display}{hint}')
                self.action_btn.set_idle('启动游戏')
                self.action_btn.setToolTip('；'.join(issues))
                return
        self._selected_version_fatal = False
        self.login_notice_label.hide()
        self._set_version_display('点击「选择版本」从列表中选取', color='rgba(255,255,255,0.7)')
        self.status_label.setText('未选择版本')
        self.settings_btn.setEnabled(False)
        self.mod_manager_btn.hide()
        self.select_version_btn.hide()
        self.action_btn.setText('选择版本')
        self.action_btn.setStyleSheet('\n            QPushButton {\n                background: rgba(128,128,128,0.32);\n                color: palette(text);\n                border: none;\n                border-radius: 8px;\n            }\n            QPushButton:hover { background: rgba(128,128,128,0.42); }\n            QPushButton:pressed { background: rgba(128,128,128,0.24); }\n        ')
        self.action_btn.setEnabled(True)

    def _refresh_selector_list(self):
        versions = self.version_mgr.get_installed_versions()
        self._update_selector_list(versions)

    def _update_selector_list(self, versions):
        self.fav_list.setUpdatesEnabled(False)
        self.mod_list.setUpdatesEnabled(False)
        self.vanilla_list.setUpdatesEnabled(False)
        self.fav_list.clear()
        self.mod_list.clear()
        self.vanilla_list.clear()
        if not versions:
            self.fav_group.setVisible(False)
            self.mod_group.setVisible(False)
            self.vanilla_group.setVisible(False)
            self.selector_empty.show()
            self.selector_layout.setStretch(self._selector_empty_index, 1)
            self.selector_layout.setStretch(self._selector_bottom_stretch, 0)
            self.selector_layout.invalidate()
            self.fav_list.setUpdatesEnabled(True)
            self.mod_list.setUpdatesEnabled(True)
            self.vanilla_list.setUpdatesEnabled(True)
            return
        self.selector_empty.hide()
        self.selector_layout.setStretch(self._selector_empty_index, 0)
        self.selector_layout.setStretch(self._selector_bottom_stretch, 1)
        self.selector_layout.invalidate()
        favorites = self.config.get('favorited_versions', [])
        mod_versions, vanilla_versions = self._categorize_versions(versions)
        fav_versions = [v for v in versions if v.get('id') in favorites]
        self.fav_group.setVisible(bool(fav_versions))
        if fav_versions:
            self.fav_group.setTitle(f'收藏的版本列表 ({len(fav_versions)})')
            for v in fav_versions:
                self._add_item_to_list(self.fav_list, v, favorites)
        self.mod_group.setVisible(bool(mod_versions))
        if mod_versions:
            self.mod_group.setTitle(f'可安装Mod版本 ({len(mod_versions)})')
            for v in mod_versions:
                self._add_item_to_list(self.mod_list, v, favorites)
        self.vanilla_group.setVisible(bool(vanilla_versions))
        if vanilla_versions:
            self.vanilla_group.setTitle(f'原版 ({len(vanilla_versions)})')
            for v in vanilla_versions:
                self._add_item_to_list(self.vanilla_list, v, favorites)
        self.fav_list.setUpdatesEnabled(True)
        self.mod_list.setUpdatesEnabled(True)
        self.vanilla_list.setUpdatesEnabled(True)
        self._adjust_list_height(self.fav_list)
        self._adjust_list_height(self.mod_list)
        self._adjust_list_height(self.vanilla_list)

    def _adjust_list_height(self, list_widget):
        count = list_widget.count()
        if count == 0:
            list_widget.setFixedHeight(0)
            return
        total = 0
        for i in range(count):
            item = list_widget.item(i)
            widget = list_widget.itemWidget(item)
            if widget:
                total += widget.sizeHint().height()
            else:
                total += 30
        total += list_widget.frameWidth() * 2 + 4
        list_widget.setFixedHeight(total)

    def _add_item_to_list(self, list_widget, version, favorites):
        version_id = str((version or {}).get('id') or '')
        is_fav = version_id in favorites
        widget = VersionItemWidget(version_id, str((version or {}).get('type') or ''), is_fav, loader=str((version or {}).get('loader') or ''), parent=list_widget)
        widget.version_selected.connect(self._on_version_clicked)
        item = QListWidgetItem()
        item.setSizeHint(widget.sizeHint())
        list_widget.addItem(item)
        list_widget.setItemWidget(item, widget)

    def _show_selector_dir_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        path = str(self.selector_dir_combo.currentData() or '')
        if not path:
            try:
                path = str(self.config.get_mc_dir() or '')
            except Exception:
                path = ''
        menu = QMenu(self)
        open_action = menu.addAction('打开文件夹位置')
        open_action.setEnabled(bool(path) and Path(path).is_dir())
        menu.addSeparator()
        add_action = menu.addAction('添加目录')
        remove_action = menu.addAction('删除目录')
        remove_action.setEnabled(bool(self.selector_dir_combo.currentData()))
        chosen = menu.exec(self.selector_dir_combo.mapToGlobal(pos))
        if chosen is open_action and path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif chosen is add_action:
            self._on_selector_dir_add()
        elif chosen is remove_action:
            self._on_selector_dir_remove()

    # ---------- 游戏目录（选择版本页右上角） ----------
    @staticmethod
    def _dir_key(path):
        try:
            import os as _os
            return _os.path.normcase(_os.path.abspath(str(path)))
        except Exception:
            return str(path or '')

    def _saved_game_dirs(self):
        dirs = []
        try:
            for item in self.config.get('game_dirs', []) or []:
                path = str(item or '').strip()
                if path and Path(path).is_dir():
                    if not any(self._dir_key(d) == self._dir_key(path) for d in dirs):
                        dirs.append(path)
        except Exception:
            pass
        current = ''
        try:
            current = str(self.config.get_mc_dir() or '')
        except Exception:
            current = ''
        if current and not any(self._dir_key(d) == self._dir_key(current) for d in dirs):
            dirs.insert(0, current)
        return dirs

    def _refresh_selector_dir_combo(self, select_path=''):
        if not hasattr(self, 'selector_dir_combo'):
            return
        self._refreshing_dirs = True
        try:
            self.selector_dir_combo.blockSignals(True)
            self.selector_dir_combo.clear()
            dirs = self._saved_game_dirs()
            for path in dirs:
                self.selector_dir_combo.addItem(path, path)
            if not dirs:
                self.selector_dir_combo.addItem('未选择游戏文件夹', '')
            desired = select_path or str(self.config.get('selected_game_dir', '') or '')
            if not desired:
                try:
                    desired = str(self.config.get_mc_dir() or '')
                except Exception:
                    desired = ''
            index = self.selector_dir_combo.findData(desired)
            self.selector_dir_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.selector_dir_combo.blockSignals(False)
            self._refreshing_dirs = False

    def _on_selector_dir_changed(self, index):
        if getattr(self, '_refreshing_dirs', False):
            return
        path = self.selector_dir_combo.itemData(index) or ''
        if not path or not Path(path).is_dir():
            return
        try:
            if self._dir_key(path) == self._dir_key(self.config.get_mc_dir()):
                return
        except Exception:
            pass
        self._apply_selector_game_dir(path)

    def _apply_selector_game_dir(self, path):
        self.config.set('selected_game_dir', path, auto_save=False)
        self.config.set('mc_dir', path, auto_save=False)
        self.config.save()
        self.reload_game_dir(path)
        self._refresh_selector_dir_combo(path)
        self.game_dir_changed.emit(path)

    def _on_selector_dir_add(self):
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, '选择游戏文件夹（.minecraft）')
        if not path:
            return
        path = str(Path(path))
        dirs = list(self.config.get('game_dirs', []) or [])
        if not any(self._dir_key(d) == self._dir_key(path) for d in dirs):
            dirs.append(path)
            self.config.set('game_dirs', dirs)
            self.config.save()
        self._apply_selector_game_dir(path)

    def _on_selector_dir_remove(self):
        path = self.selector_dir_combo.currentData() or ''
        if not path:
            return
        dirs = list(self.config.get('game_dirs', []) or [])
        key = self._dir_key(path)
        remaining = [d for d in dirs if self._dir_key(d) != key]
        try:
            current_key = self._dir_key(self.config.get_mc_dir())
        except Exception:
            current_key = ''
        if self._dir_key(path) == current_key:
            next_dir = next((d for d in remaining if Path(d).is_dir()), '')
            if not next_dir:
                QMessageBox.information(self, '无法移除', '至少需要保留一个游戏文件夹。')
                return
            self.config.set('game_dirs', remaining)
            self.config.save()
            self._apply_selector_game_dir(next_dir)
            return
        self.config.set('game_dirs', remaining)
        self.config.save()
        self._refresh_selector_dir_combo()

    def _show_version_selector(self):
        self.stacked.setCurrentIndex(1)
        self._refresh_selector_dir_combo()
        versions = self.version_mgr.get_installed_versions()
        self._update_selector_list(versions)

    def _dispose_mod_manager_page(self):
        page = self._mod_manager_page
        self._mod_manager_page = None
        if page is not None:
            try:
                page.shutdown()
            except Exception:
                pass
            try:
                self.stacked.removeWidget(page)
                page.deleteLater()
            except Exception:
                pass

    def _go_back_home(self):
        self._dispose_mod_manager_page()
        self.stacked.setCurrentIndex(0)
        versions = self.version_mgr.get_installed_versions()
        self._update_home_display(versions)

    def _on_settings_clicked_from_home(self):
        if not self.selected_version_id:
            QMessageBox.warning(self, '提示', '请先选择一个版本')
            return
        self.open_game_settings.emit(self.selected_version_id)

    def _is_modded_version(self, version_id):
        if not version_id:
            return False
        lower = version_id.lower()
        if any((key in lower for key in ('forge', 'fabric', 'neoforge', 'quilt', 'optifine'))):
            return True
        mc_dir = Path(self.config.get_mc_dir())
        version_dir = mc_dir / 'versions' / version_id
        if (version_dir / 'pmgl_pack.json').is_file():
            return True
        json_path = version_dir / f'{version_id}.json'
        try:
            data = json.loads(json_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False
        blob = json.dumps(data, ensure_ascii=False).lower()
        markers = ('fabric-loader', 'net.fabricmc', 'quilt-loader', 'org.quiltmc', 'minecraftforge', 'net.minecraftforge', 'bootstraplauncher', 'net.neoforged', 'neoforge', 'optifine')
        return any((marker in blob for marker in markers))

    def _open_mod_manager_home(self):
        if not self.selected_version_id:
            return
        self._dispose_mod_manager_page()
        page = ModManagerPage(self.selected_version_id, self)
        page.back_requested.connect(self._go_back_home)
        self._mod_manager_page = page
        self.stacked.addWidget(page)
        self.stacked.setCurrentWidget(page)

    def _nickname(self, version_id):
        nicknames = self.config.get('version_nicknames', {}) or {}
        return nicknames.get(version_id, version_id)

    def _launch_button_style(self):
        return '\n            QPushButton {\n                background: #0078d4;\n                color: white;\n                border: none;\n                border-radius: 8px;\n            }\n            QPushButton:hover { background: #106ebe; }\n            QPushButton:pressed { background: #005a9e; }\n            QPushButton:disabled { background: rgba(128,128,128,0.25); color: #8a8f98; }\n        '

    def _fatal_button_style(self):
        return (
            'QPushButton { background: rgba(128,128,128,0.25);'
            ' color: #8a8f98; border: none; border-radius: 8px; }'
            'QPushButton:disabled { background: rgba(128,128,128,0.25);'
            ' color: #8a8f98; }'
        )

    def _restore_action_button(self):
        self._launch_preparing = False
        if self._selected_version_fatal:
            self.action_btn.set_disabled_state('错误版本无法启动')
        else:
            self.action_btn.set_idle('启动游戏')

    def _launch_option_enabled(self, key):
        '''读取当前版本的高级选项，未设置时继承全局默认值。'''

        try:
            settings = self.launch_mgr.get_version_settings(self.selected_version_id) or {}
            value = settings.get(key)
            if value is None or value == '':
                value = self.config.get(key, False)
            return bool(value)
        except Exception:
            return False

    def _on_action_clicked(self):
        if self._launch_preparing:
            self._cancel_launch_prepare()
            return
        if self._selected_version_fatal:
            QMessageBox.warning(self, '无法启动', '当前选择的是错误版本，无法启动。\n请删除后重新安装该版本。')
            return
        if self._starting_worker is not None:
            QMessageBox.information(self, '请稍候', '上一个游戏正在启动，请等待启动成功后再启动下一个。')
            return
        login_type = self.config.get('login_type', 'offline')
        try:
            version_login = self.launch_mgr.get_version_settings(self.selected_version_id).get('login_mode', 'global')
        except Exception:
            version_login = 'global'
        if version_login not in ('', 'global'):
            login_type = version_login
        if login_type == 'offline' and not self.launch_mgr.auth.get_offline_name():
            from ui.widgets.dark_dialog import DarkQuestionDialog
            dialog = DarkQuestionDialog(self, '需要登录', '要进行游戏需要先登录。设置用户名。', actions=[('设置用户名', 'set', True), ('取消', 'cancel', False)])
            dialog.exec()
            if dialog.choice == 'set':
                self.request_offline_login.emit()
                return
            self.status_label.setText('请先设置用户名后再启动')
            return
        versions = self.version_mgr.get_installed_versions()
        if not versions:
            self.request_switch_page.emit(1)
            return
        if not self.selected_version_id:
            try:
                if DownloadManager.instance().active_tasks():
                    self.status_label.setText('版本正在下载，请等待安装完成后再启动')
                    from ui.widgets.toast import show_toast
                    show_toast('版本正在下载，请等待安装完成后再启动', 'info', self)
                    return
            except Exception:
                pass
            self._show_version_selector()
            return
        version_id = self.selected_version_id
        if self._preflight_worker is not None and self._preflight_worker.isRunning():
            return
        self.status_label.setText('正在检查 Java 环境...')
        self._launch_preparing = True
        self._preflight_cancelled = False
        self.action_btn.set_working('取消', 0.0, animate=False, show_percent=False)
        worker = JavaPreflightWorker(self.launch_mgr, version_id)
        self._preflight_worker = worker
        worker.done.connect(
            (lambda state, w=worker, v=version_id: self._on_java_preflight_done(state, w, v))
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _apply_downloaded_java(self, version_id, path):
        if not path or not self.launch_mgr.apply_auto_java(version_id, path):
            return False
        from ui.widgets.toast import show_toast
        show_toast('Java 已下载并应用', 'success', self)
        try:
            from core.java_scan import JavaScanService
            JavaScanService.instance().start(force=True)
        except Exception:
            pass
        self._begin_launch_prepare(version_id)
        return True

    def _on_java_preflight_done(self, java_state, worker, version_id):
        if self._preflight_worker is worker:
            self._preflight_worker = None
        if self._preflight_cancelled:
            self._restore_action_button()
            return
        if not version_id or version_id != self.selected_version_id:
            self._restore_action_button()
            return
        java_status = java_state.get('status')
        min_java = int(java_state.get('min_major') or 17)
        force_java_path = ''
        if java_status == 'missing':
            from ui.widgets.java_dialogs import ask_java_missing
            path = ask_java_missing(self, min_java)
            if self._apply_downloaded_java(version_id, path):
                return
            self._restore_action_button()
            self.status_label.setText('未找到有效的 Java，请安装后重试')
            return
        if java_status == 'mismatch':
            from ui.widgets.java_dialogs import ask_java_mismatch, ask_java_missing
            choice = ask_java_mismatch(self, int(java_state.get('selected_major') or 0), min_java, int(java_state.get('max_major') or min_java))
            if choice == 'auto':
                auto_path = str(java_state.get('auto_path') or '')
                if auto_path and self.launch_mgr.apply_auto_java(version_id, auto_path):
                    self.status_label.setText(f'已自动选择 Java {java_state.get("auto_major")}')
                    from ui.widgets.toast import show_toast
                    show_toast(f'已自动选择 Java {java_state.get("auto_major")}', 'success', self)
                else:
                    path = ask_java_missing(self, min_java)
                    if self._apply_downloaded_java(version_id, path):
                        return
                    self._restore_action_button()
                    self.status_label.setText('没有找到适合该版本的 Java')
                    return
            elif choice == 'continue':
                force_java_path = str(java_state.get('selected_path') or '')
            else:
                self._restore_action_button()
                self.status_label.setText('已取消启动')
                return
        self._begin_launch_prepare(version_id, force_java_path)

    def _begin_launch_prepare(self, version_id, force_java_path=''):
        if self._prepare_worker is not None and self._prepare_worker.isRunning():
            return
        nickname = self._nickname(version_id)
        self._launch_preparing = True
        self._prepare_cancelled = False
        self.action_btn.set_working('取消', 0.0, animate=False, show_percent=True)
        self.status_label.setText(f'正在准备启动 {nickname} ...')
        worker = LaunchPrepareWorker(self.launch_mgr, version_id, force_java_path)
        self._prepare_worker = worker
        self._prepare_done = False
        self._prepare_task_id = ''
        worker.success.connect(self._on_launch_prepared)
        worker.error.connect(self._on_launch_prepare_error)
        worker.status.connect(self._on_launch_prepare_status)
        worker.progress.connect(self._on_launch_prepare_progress)
        worker.finished.connect((lambda w=worker: self._on_launch_prepare_finished(w)))
        worker.start()

    def _on_launch_prepare_status(self, text):
        self.status_label.setText(text)

    def _on_launch_prepare_progress(self, value):
        try:
            percent = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return
        self.status_label.setText(f'正在准备启动 {percent}%')
        self.action_btn.set_working('取消', percent / 100.0, show_percent=True)

    def _on_launch_prepared(self, command, version_id):
        self._prepare_done = True
        self._launch_preparing = False
        self._start_game(command, version_id)

    def _on_launch_prepare_error(self, message):
        self._prepare_done = True
        self._launch_preparing = False
        self._restore_action_button()
        if self._prepare_cancelled:
            self.status_label.setText('已取消启动准备')
            return
        self.status_label.setText('启动准备失败')
        from core.net import is_network_error_text, network_available
        if is_network_error_text(message) or not network_available():
            from ui.widgets.toast import notify_network_failure
            self.status_label.setText('未连接到互联网')
            notify_network_failure(self, '下载文件')
            return
        from ui.widgets.dark_dialog import show_severe_error
        show_severe_error(self, '启动失败', message)

    def _cancel_launch_prepare(self):
        self._prepare_cancelled = True
        self._launch_preparing = False
        self._preflight_cancelled = True
        worker = self._prepare_worker
        try:
            if worker is not None and worker.isRunning():
                worker.cancel()
        except RuntimeError:
            pass
        self._restore_action_button()
        self.status_label.setText('已取消启动准备')

    def _on_launch_prepare_finished(self, worker):
        self._launch_preparing = False
        if self._prepare_worker is worker:
            self._prepare_worker = None
        if not self._prepare_done:
            cancelled = bool(getattr(worker, '_cancelled', False))
            if not cancelled:
                self.status_label.setText('启动准备意外中断，请重试')
                self._restore_action_button()
                from ui.widgets.toast import show_toast
                show_toast('启动准备意外中断，请重试', 'error', self)
        if self._starting_worker is None and not self._is_running:
            self._restore_action_button()
        worker.deleteLater()

    def _start_game(self, command, version_id):
        import time as _time
        nickname = self._nickname(version_id)
        self.action_btn.set_working('正在启动...', 1.0, animate=False, show_percent=False, starting=True)
        self.status_label.setText(f'正在启动 {nickname} ...')
        mc_dir = Path(self.config.get_mc_dir())
        log_dir = LOG_DIR
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log_dir = mc_dir / 'logs'
        safe_name = re.sub('[<>:"/\\\\|?*\\x00-\\x1f]', '_', nickname)[:60]
        log_path = log_dir / f'game-{safe_name}-{_time.strftime("%Y%m%d-%H%M%S")}.log'
        try:
            log_handle = open(log_path, 'w', encoding='utf-8', errors='replace')
        except OSError:
            log_handle = None
        pre_launch = self.launch_mgr.get_pre_launch_command(version_id)
        window_title = getattr(self.launch_mgr, 'last_window_title', '') or ''
        worker = LaunchWorker(command, self.config.get_mc_dir(), pre_launch, window_title)
        info = {'version_id': version_id, 'nickname': nickname, 'log_path': log_path, 'log_handle': log_handle, 'lines': [], 'user_stopped': False, 'started': False, 'pid': None}
        self._instance_info[worker] = info
        self._instances.append(worker)
        self._launch_worker = worker
        self._starting_worker = worker
        worker.launch_success.connect((lambda w=worker: self._on_launch_success(w)))
        worker.launch_error.connect((lambda msg, w=worker: self._on_launch_error(w, msg)))
        worker.process_exited.connect((lambda code, w=worker: self._on_process_exited(w, code)))
        worker.output_received.connect((lambda line, w=worker: self._append_log(line, w)))
        worker.window_ready.connect((lambda w=worker: self._on_game_window_ready(w)))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _add_running_row(self, worker):
        info = self._instance_info.get(worker)
        if not info:
            return
        row = QWidget()
        row.setStyleSheet('background: rgba(255,255,255,0.04); border-radius: 6px;')
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 8)
        pid_text = f'  (PID {info["pid"]})' if info.get('pid') else ''
        label = QLabel(f'运行中：{info["nickname"]}{pid_text}')
        label.setStyleSheet('color: #9ccaff; font-size: 13px; background: transparent;')
        btn = QPushButton(f'结束 {info["nickname"]} 的进程')
        btn.setStyleSheet('\n            QPushButton { background:#6c2a2a; color:white; border:none;\n                          border-radius:5px; padding:5px 12px; }\n            QPushButton:hover { background:#8a3a3a; }\n            QPushButton:disabled { background:#333; color:#777; }\n        ')
        btn.clicked.connect((lambda checked=False, w=worker: self._stop_game(w)))
        layout.addWidget(label, 1)
        layout.addWidget(btn)
        self._running_rows[worker] = row, btn
        self.running_layout.addWidget(row)
        self.running_group.setTitle(f'运行中的游戏 ({len(self._running_rows)})')
        self.running_group.show()

    def _remove_running_row(self, worker):
        item = self._running_rows.pop(worker, None)
        if item:
            row, _btn = item
            row.deleteLater()
        self.running_group.setTitle(f'运行中的游戏 ({len(self._running_rows)})')
        if not self._running_rows:
            self.running_group.hide()

    def _stop_game(self, worker):
        if not worker:
            return
        info = self._instance_info.get(worker)
        if info:
            info['user_stopped'] = True
        item = self._running_rows.get(worker)
        if item:
            item[1].setEnabled(False)
        self.status_label.setText('正在终止游戏进程...')
        self._append_log('正在终止游戏进程...', worker)
        worker.stop_game()

    def _on_launch_success(self, worker):
        info = self._instance_info.get(worker)
        if not info:
            return
        info['started'] = True
        try:
            proc = getattr(worker, '_process', None)
            if proc is not None:
                info['pid'] = proc.pid
        except Exception:
            pass
        # 进程启动不等于游戏窗口已出现；保持按钮为进度状态，等 window_ready 再恢复
        self.action_btn.set_working('游戏启动中...', 1.0, animate=False, show_percent=False, starting=True)
        self.status_label.setText(f'{info["nickname"]} 进程已启动，正在等待游戏窗口...')
        self._append_log(f'{info["nickname"]} 进程已启动', worker)
        try:
            set_process_priority(info.get('pid'), str(self.config.get('game_process_priority', 'normal') or 'normal'))
        except Exception:
            pass
        self._add_running_row(worker)
        # 游戏窗口真正出现后再隐藏/最小化启动器，并允许启动其他版本
        QTimer.singleShot(300, (lambda w=worker: self._poll_game_window(w)))
        # 非 Windows 或窗口检测异常时的兜底，避免按钮永久停在“游戏启动中”
        delay = 90000 if sys.platform == 'win32' else 4000
        QTimer.singleShot(delay, (lambda w=worker: self._on_game_window_ready(w)))

    def _on_game_window_ready(self, worker):
        if self._starting_worker is not worker:
            return
        try:
            if not worker.isRunning():
                return
        except RuntimeError:
            return
        self._starting_worker = None
        self.action_btn.set_idle('启动游戏')
        info = self._instance_info.get(worker) or {}
        nickname = info.get('nickname') or ''
        if nickname:
            self.status_label.setText(f'{nickname} 已启动')
            from ui.widgets.toast import show_toast
            show_toast(f'{nickname} 启动成功', 'success', self)
        self._apply_launcher_visibility()

    def _poll_game_window(self, worker):
        if self._starting_worker is not worker:
            return
        try:
            if not worker.isRunning():
                return
        except RuntimeError:
            return
        info = self._instance_info.get(worker) or {}
        pid = int(info.get('pid') or 0)
        found = False
        if pid:
            try:
                from core.launch import _game_window_handle
                found = bool(_game_window_handle(pid))
            except Exception:
                found = False
        if found:
            self._on_game_window_ready(worker)
            return
        QTimer.singleShot(500, (lambda w=worker: self._poll_game_window(w)))

    def _on_launch_error(self, worker, msg):
        info = self._instance_info.get(worker)
        if self._starting_worker is worker:
            self._starting_worker = None
        self.action_btn.set_idle('启动游戏')
        self.status_label.setText('启动失败')
        self._append_log(f'启动失败：{msg}', worker)
        if info:
            self._finalize_instance(worker)
        from ui.widgets.dark_dialog import show_severe_error
        show_severe_error(self, '启动失败', msg)
        self._refresh_all()

    def _on_process_exited(self, worker, exit_code):
        info = self._instance_info.get(worker)
        if not info:
            return
        nickname = info['nickname']
        user_stopped = info.get('user_stopped', False)
        started = info.get('started', False)
        if self._starting_worker is worker:
            self._starting_worker = None
        log_path = info.get('log_path')
        lines = list(info.get('lines', []))
        self.action_btn.set_idle('启动游戏')
        self._finalize_instance(worker)
        if exit_code == 0:
            self.status_label.setText(f'{nickname} 已正常退出')
            self._append_log(f'{nickname} 进程退出，退出码：0')
            from ui.widgets.toast import show_toast
            show_toast(f'{nickname} 已正常退出', 'success', self)
            self._restore_launcher_after_game()
        elif user_stopped:
            self.status_label.setText(f'{nickname} 已被终止')
            self._append_log(f'{nickname} 已被手动终止')
            self._restore_launcher_after_game()
        else:
            self.status_label.setText(f'{nickname} 启动失败（退出码 {exit_code}）')
            from core.crash import analyze_crash
            from ui.widgets.crash_dialog import CrashDialog
            report = analyze_crash(lines, nickname, exit_code)
            extra = []
            try:
                mc_dir = Path(self.config.get_mc_dir())
                latest = mc_dir / 'logs' / 'latest.log'
                if latest.exists():
                    extra.append(latest)
                crash_dir = mc_dir / 'crash-reports'
                if crash_dir.exists():
                    extra.extend(sorted(crash_dir.glob('*.txt'), key=(lambda p: p.stat().st_mtime), reverse=True)[:3])
            except Exception:
                pass
            CrashDialog(report, log_path, extra, self).exec()
        self._refresh_all()

    def _apply_launcher_visibility(self):
        mode = str(self.config.get('launcher_visibility', 'keep') or 'keep')
        window = self.window()
        if mode == 'close':
            window.close()
            return
        if mode in ('hide_close', 'hide_restore'):
            self._launcher_hidden_by_game = True
            window.hide()
            return
        if mode == 'minimize':
            window.showMinimized()

    def _restore_launcher_after_game(self):
        if self._instances:
            return
        mode = str(self.config.get('launcher_visibility', 'keep') or 'keep')
        if mode == 'hide_close':
            QApplication.instance().quit()
            return
        if mode in ('hide_restore', 'minimize'):
            window = self.window()
            window.showNormal()
            window.raise_()
            window.activateWindow()
            self._launcher_hidden_by_game = False

    def _finalize_instance(self, worker):
        info = self._instance_info.pop(worker, None)
        if info:
            handle = info.get('log_handle')
            if handle:
                try:
                    handle.flush()
                    handle.close()
                except Exception:
                    pass
        if worker in self._instances:
            self._instances.remove(worker)
        if self._launch_worker is worker:
            self._launch_worker = self._instances[-1] if self._instances else None
        self._remove_running_row(worker)

    def _append_log(self, text, worker=None):
        if text is None:
            return
        if not isinstance(text, str):
            text = str(text)
        info = self._instance_info.get(worker)
        if info:
            info['lines'].append(text)
            if len(info['lines']) > 8000:
                info['lines'] = info['lines'][-4000:]
            handle = info.get('log_handle')
            if handle:
                try:
                    handle.write(text + '\n')
                    handle.flush()
                except Exception:
                    pass

    def refresh_versions(self):
        '''刷新版本列表（供外部调用，如 version_page 安装完成信号）'''

        self._refresh_all()

    def reload_game_dir(self, path: str=''):
        '''切换游戏文件夹后刷新首页版本列表。'''

        self.version_mgr = VersionManager(path or self.config.get_mc_dir())
        self._refresh_all()
        self._refresh_selector_dir_combo(path)

    def _on_version_clicked(self, version_id):
        if not version_id:
            return
        try:
            info = self.version_mgr.validate_version(version_id) or {}
        except Exception:
            info = {}
        if info.get('fatal'):
            issues = info.get('issues') or ['未知错误']
            QMessageBox.warning(
                self, '版本损坏',
                f'版本「{version_id}」存在无法自动修复的错误：\n'
                + '\n'.join(issues)
                + '\n\n该版本不能选择或启动，请删除后重新安装。',
            )
            return
        self.selected_version_id = version_id
        self.config.set('selected_version', version_id)
        self.config.save()
        versions = self.version_mgr.get_installed_versions()
        self._update_home_display(versions)
        if self.stacked.currentIndex() == 1:
            self._go_back_home()

    def shutdown(self):
        '''启动器退出时终止日志监控线程（不终止已启动的游戏进程）。'''

        self._dispose_mod_manager_page()
        preflight = self._preflight_worker
        if preflight is not None:
            try:
                if preflight.isRunning():
                    preflight.wait(1000)
            except RuntimeError:
                pass
            self._preflight_worker = None
        prepare = self._prepare_worker
        if prepare is not None:
            try:
                if prepare.isRunning():
                    prepare.cancel()
                    if not prepare.wait(1500):
                        prepare.terminate()
                        prepare.wait(500)
            except RuntimeError:
                pass
            self._prepare_worker = None
        self._launch_preparing = False
        self._prepare_task_id = ''
        for worker in list(self._instances):
            info = self._instance_info.get(worker)
            if info and info.get('log_handle'):
                try:
                    info['log_handle'].flush()
                    info['log_handle'].close()
                except Exception:
                    pass
            try:
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(500)
            except RuntimeError:
                continue
        self._instances.clear()
        self._instance_info.clear()


