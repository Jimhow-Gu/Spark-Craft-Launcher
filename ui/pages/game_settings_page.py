'''
游戏设置页面 - 独立页面，所有更改即时保存
Java 列表与全局设置同步，内存分配支持自定义/跟随全局
新增：版本文件夹、存档文件夹、Mod文件夹 快捷按钮
'''

import json
import os
import re
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QSlider, QLineEdit, QCheckBox, QGroupBox, QScrollArea, QRadioButton, QButtonGroup, QProgressBar, QMessageBox, QFileDialog, QFrame, QGridLayout, QTabWidget, QGraphicsOpacityEffect)
from PySide6.QtCore import (Qt, Signal, QTimer, QUrl, QEasingCurve, QPropertyAnimation, QThread)
from PySide6.QtGui import QFont, QDesktopServices, QPixmap
from ui.widgets.frameless_dialog import FramelessInputDialog
from ui.version_meta import (
    LOADER_LABELS, default_icon_value, detect_installed_loader, display_name,
    version_data, version_description, version_icon, version_settings,
)
from core.app_font import ui_font
from ui.widgets.back_button import BackButton
from ui.widgets.painted_icons import painted_icon
from ui.widgets.settings_widgets import hidpi_pixmap
from core.config import ConfigManager
from core.download_manager import DownloadManager
from core.launch import LaunchManager
from core.repair import GameRepairWorker
from ui.widgets.advanced_options import AdvancedOptionsPanel
from ui.widgets.form_styles import RADIO_BUTTON_STYLE
from ui.widgets.smooth_progress import SmoothProgressBar
_DETACHED_REPAIR_WORKERS = set()
_CLEANUP_REGISTERED = False

def _shutdown_detached_repairs():
    for worker in list(_DETACHED_REPAIR_WORKERS):
        try:
            if worker.isRunning() and not worker.wait(1200):
                worker.terminate()
                worker.wait(300)
        except RuntimeError:
            continue
    _DETACHED_REPAIR_WORKERS.clear()

class _ExportBottomBar(QWidget):
    '''导出页底部悬浮按钮条：背景透明，内容从其下穿过，按钮保持在最上层。'''

    def __init__(self, tab):
        super().__init__(tab)
        from PySide6.QtCore import QEvent
        self._tab = tab
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(78)
        tab.installEventFilter(self)
        self.raise_()
        # 父部件已可见时创建的子部件默认隐藏，必须显式显示
        self.show()

    def _sync_geometry(self):
        self.setGeometry(
            0, max(0, self._tab.height() - self.height()),
            self._tab.width(), self.height(),
        )

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self._tab and event.type() == QEvent.Resize:
            self._sync_geometry()
        return False

    def showEvent(self, event):
        self._sync_geometry()
        super().showEvent(event)


class _JavaHintWorker(QThread):
    '''后台探测“将会使用”的 Java（可能触发全盘扫描与子进程），避免阻塞界面。'''

    done = Signal(str, str)

    def __init__(self, launch_mgr, version_id, selected_path):
        super().__init__()
        self.launch_mgr = launch_mgr
        self.version_id = version_id
        self.selected_path = selected_path

    def run(self):
        path = self.selected_path
        if not path:
            try:
                state = self.launch_mgr.preflight_java(self.version_id) or {}
                path = str(state.get('selected_path') or state.get('auto_path') or '')
            except Exception:
                path = ''
        version = ''
        if path and os.path.isfile(path):
            try:
                version = self.launch_mgr._get_java_version(path) or ''
            except Exception:
                version = ''
        self.done.emit(path, version)


def has_active_repairs() -> bool:
    for worker in list(_DETACHED_REPAIR_WORKERS):
        try:
            if worker.isRunning() and not getattr(worker, '_cancelled', False):
                return True
        except RuntimeError:
            continue
    return False

def _register_cleanup():
    global _CLEANUP_REGISTERED
    if _CLEANUP_REGISTERED:
        return
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(_shutdown_detached_repairs)
        _CLEANUP_REGISTERED = True

class GameSettingsPage(QWidget):
    back_requested = Signal()
    version_meta_changed = Signal(str)
    global_settings_requested = Signal()
    def __init__(self, version_id, parent=None):
        super().__init__(parent)
        self.setObjectName('GameSettingsPage')
        self.version_id = version_id
        self.config = ConfigManager()
        self.launch_mgr = LaunchManager()
        self.settings = self.launch_mgr.get_version_settings(version_id)
        self._effective_isolated = self.launch_mgr.is_version_isolated(version_id)
        # 与 PCL/HMCL 互通：打开版本设置时回填当前隔离状态标记
        try:
            from core.interop import write_isolation_markers
            write_isolation_markers(
                Path(self.config.get_mc_dir()) / 'versions' / version_id,
                self._effective_isolated,
            )
        except Exception:
            pass
        self._repair_worker = None
        self._repair_task_id = ''
        self._tab_animation = None
        self._tab_effect = None
        self._tab_effect_widget = None
        self._installed_loader_kind = self._detect_installed_loader_kind()
        self._init_ui()
        self._load_settings()
        _register_cleanup()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(40, 24, 40, 8)
        header_layout.setSpacing(10)
        back_btn = BackButton()
        back_btn.clicked.connect(self.back_requested.emit)
        title = QLabel(f'游戏设置 - {self.version_id}')
        self.page_title_label = title
        title.setFont(ui_font(24, QFont.DemiBold))
        title.setStyleSheet('color: #f0f0f0;')
        header_layout.addWidget(back_btn)
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        main_layout.addWidget(header)

        def make_page():
            page_scroll = QScrollArea()
            page_scroll.setWidgetResizable(True)
            page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            page_scroll.setStyleSheet('\n            QScrollArea { background: transparent; border: none; }\n            QScrollBar:vertical { background: #2a2a2a; width: 12px; border-radius: 6px; }\n            QScrollBar::handle:vertical { background: #555; border-radius: 6px; min-height: 20px; }\n            QScrollBar::handle:vertical:hover { background: #777; }\n            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }\n        ')
            page_content = QWidget()
            page_layout = QVBoxLayout(page_content)
            page_layout.setContentsMargins(0, 16, 0, 30)
            page_layout.setSpacing(20)
            page_scroll.setWidget(page_content)
            return page_scroll, page_layout

        overview_scroll, overview_layout = make_page()
        settings_scroll, settings_layout = make_page()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        self.tabs.setStyleSheet(
            'QTabWidget { border: none; background: transparent; }'
            'QTabWidget::pane { border: none; background: transparent; }'
            'QTabWidget::tab-bar { border: none; background: transparent; left: 0px; }'
            'QTabBar { border: none; background: transparent; }'
            'QTabBar::tab { background: rgba(255,255,255,0.11); color: #f0f0f0;'
            ' border: 1px solid rgba(255,255,255,0.16); padding: 8px 22px; margin-right: 6px; margin-bottom: 8px;'
            ' border-radius: 8px; font-size: 11pt; }'
            'QTabBar::tab:selected { background: #0078d4; color: white; border-color: #0078d4; }'
            'QTabBar::tab:hover { background: rgba(255,255,255,0.18); }'
        )
        self.tabs.addTab(overview_scroll, '概览')
        self.tabs.setTabIcon(self.tabs.count() - 1, painted_icon('overview', 16, '#aab4c0'))
        self.tabs.addTab(settings_scroll, '设置')
        self.tabs.setTabIcon(self.tabs.count() - 1, painted_icon('gear', 16, '#aab4c0'))
        self.modify_tab = QWidget()
        self.modify_tab_layout = QVBoxLayout(self.modify_tab)
        self.modify_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(self.modify_tab, '修改')
        self.tabs.setTabIcon(self.tabs.count() - 1, painted_icon('pencil', 16, '#aab4c0'))
        self.mod_tab = QWidget()
        self.mod_tab_layout = QVBoxLayout(self.mod_tab)
        self.mod_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(self.mod_tab, 'Mod管理')
        self.tabs.setTabIcon(self.tabs.count() - 1, painted_icon('components', 16, '#aab4c0'))
        self.export_tab = QWidget()
        self.export_tab_layout = QVBoxLayout(self.export_tab)
        self.export_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.export_tab_layout.setSpacing(0)
        self.tabs.addTab(self.export_tab, '导出')
        self.tabs.setTabIcon(self.tabs.count() - 1, painted_icon('export', 16, '#aab4c0'))
        self._export_tab_ready = False
        self._export_worker = None
        self._export_task_id = ''
        self._mod_manager_page = None
        self._loader_modify_page = None
        self._mod_tab_ready = False
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.currentChanged.connect(self._animate_tab_switch)
        settings_hint = QLabel('这些设置只对该游戏版本生效，不影响其他版本。')
        settings_hint.setWordWrap(True)
        settings_hint.setStyleSheet('color:#4da3ff; font-size:12px; background:rgba(0,120,212,0.12); border-left:3px solid #4da3ff; border-radius:4px; padding:8px 12px;')
        settings_layout.addWidget(settings_hint)
        version_card = QFrame()
        version_card.setObjectName('VersionHeaderCard')
        version_card.setStyleSheet('\n            QFrame#VersionHeaderCard { background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.22); border-radius: 12px; }\n            QLabel { background: transparent; border: none; }\n        ')
        version_card_layout = QHBoxLayout(version_card)
        version_card_layout.setContentsMargins(18, 14, 18, 14)
        version_card_layout.setSpacing(14)
        self.version_icon_label = QLabel()
        self.version_icon_label.setFixedSize(52, 52)
        self.version_icon_label.setAlignment(Qt.AlignCenter)
        version_card_layout.addWidget(self.version_icon_label, 0, Qt.AlignVCenter)
        version_text_layout = QVBoxLayout()
        version_text_layout.setContentsMargins(0, 0, 0, 0)
        version_text_layout.setSpacing(2)
        self.version_name_label = QLabel()
        self.version_name_label.setFont(ui_font(17, QFont.DemiBold))
        self.version_name_label.setStyleSheet('color: rgba(255,255,255,0.94);')
        self.version_desc_label = QLabel()
        self.version_desc_label.setStyleSheet('color: rgba(255,255,255,0.52); font-size: 12px;')
        self.version_desc_label.setWordWrap(True)
        version_text_layout.addWidget(self.version_name_label)
        version_text_layout.addWidget(self.version_desc_label)
        version_card_layout.addLayout(version_text_layout, 1)
        overview_layout.addWidget(version_card)
        personal_group = QGroupBox('个性化')
        personal_group.setStyleSheet(self._group_style())
        personal_layout = QGridLayout(personal_group)
        personal_layout.setContentsMargins(18, 16, 18, 16)
        personal_layout.setHorizontalSpacing(14)
        personal_layout.setVerticalSpacing(10)
        personal_layout.addWidget(QLabel('图标'), 0, 0)
        self.icon_combo = QComboBox()
        self.icon_combo.setStyleSheet(self._combo_style())
        for label, value in (
            ('原版图标', 'release'), ('快照图标', 'snapshot'),
            ('Fabric', 'fabric'), ('Forge', 'forge'), ('NeoForge', 'neoforge'),
            ('OptiFine', 'optifine'), ('选择自定义图片...', '__custom__'),
        ):
            self.icon_combo.addItem(label, value)
        self.icon_combo.currentIndexChanged.connect(self._on_icon_changed)
        personal_layout.addWidget(self.icon_combo, 0, 1)
        personal_layout.addWidget(QLabel('分类'), 1, 0)
        self.category_combo = QComboBox()
        self.category_combo.setStyleSheet(self._combo_style())
        for label, value in (
            ('自动', 'auto'), ('原版', 'vanilla'),
            ('可安装 Mod', 'modded'), ('整合包', 'modpack'),
        ):
            self.category_combo.addItem(label, value)
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        personal_layout.addWidget(self.category_combo, 1, 1)
        personal_btn_row = QHBoxLayout()
        personal_btn_row.setSpacing(10)
        self.rename_version_btn = QPushButton('修改版本名')
        self.rename_version_btn.setStyleSheet(self._folder_btn_style())
        self.rename_version_btn.clicked.connect(self._rename_version_display)
        self.edit_desc_btn = QPushButton('修改版本描述')
        self.edit_desc_btn.setStyleSheet(self._folder_btn_style())
        self.edit_desc_btn.clicked.connect(self._edit_version_description)
        self.favorite_btn = QPushButton('加入收藏夹')
        self.favorite_btn.setStyleSheet(self._folder_btn_style())
        self.favorite_btn.clicked.connect(self._toggle_favorite_version)
        personal_btn_row.addWidget(self.rename_version_btn)
        personal_btn_row.addWidget(self.edit_desc_btn)
        personal_btn_row.addWidget(self.favorite_btn)
        personal_btn_row.addStretch(1)
        personal_layout.addLayout(personal_btn_row, 2, 0, 1, 2)
        overview_layout.addWidget(personal_group)
        server_group = QGroupBox('服务器')
        server_group.setStyleSheet(self._group_style())
        server_layout = QGridLayout(server_group)
        server_layout.setContentsMargins(18, 16, 18, 16)
        server_layout.setHorizontalSpacing(14)
        server_layout.setVerticalSpacing(10)
        server_layout.addWidget(QLabel('登录方式'), 0, 0)
        self.login_mode_combo = QComboBox()
        self.login_mode_combo.addItem('跟随全局', 'global')
        self.login_mode_combo.addItem('正版账号', 'microsoft')
        self.login_mode_combo.addItem('第三方账号（LittleSkin 等）', 'thirdparty')
        self.login_mode_combo.addItem('离线账号', 'offline')
        self.login_mode_combo.setStyleSheet(self._combo_style())
        self.login_mode_combo.currentIndexChanged.connect(self._on_login_mode_changed)
        server_layout.addWidget(self.login_mode_combo, 0, 1)
        server_layout.addWidget(QLabel('自动进入服务器'), 1, 0)
        self.auto_server_edit = QLineEdit()
        self.auto_server_edit.setPlaceholderText('输入服务器 IP，例如 play.example.com 或 host:25565')
        self.auto_server_edit.setStyleSheet(self._lineedit_style())
        self.auto_server_edit.editingFinished.connect(self._on_auto_server_changed)
        server_layout.addWidget(self.auto_server_edit, 1, 1)
        startup_group = QGroupBox('启动选项')
        startup_group.setStyleSheet(self._group_style())
        startup_layout = QGridLayout(startup_group)
        startup_layout.setContentsMargins(18, 16, 18, 16)
        startup_layout.setHorizontalSpacing(14)
        startup_layout.setVerticalSpacing(10)
        startup_layout.addWidget(QLabel('版本隔离'), 0, 0)
        self.iso_combo = QComboBox()
        self.iso_combo.addItem('关闭', False)
        self.iso_combo.addItem('开启', True)
        self.iso_combo.setStyleSheet(self._combo_style())
        self.iso_combo.currentIndexChanged.connect(self._on_iso_combo_changed)
        startup_layout.addWidget(self.iso_combo, 0, 1)
        startup_layout.addWidget(QLabel('游戏窗口标题'), 1, 0)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText('跟随全局设置')
        self.title_edit.textChanged.connect(self._on_title_changed)
        self.title_edit.setStyleSheet(self._lineedit_style())
        startup_layout.addWidget(self.title_edit, 1, 1)
        startup_layout.addWidget(QLabel('Java'), 2, 0)
        self.java_combo = QComboBox()
        self.java_combo.setStyleSheet(self._combo_style())
        self.java_combo.currentIndexChanged.connect(self._on_java_changed)
        startup_layout.addWidget(self.java_combo, 2, 1)
        self.java_hint_label = QLabel('')
        self.java_hint_label.setWordWrap(True)
        self.java_hint_label.setStyleSheet('color:#9ccaff; font-size:12px; background:rgba(0,120,212,0.10); border-left:3px solid #4da3ff; border-radius:4px; padding:7px 10px;')
        startup_layout.addWidget(self.java_hint_label, 3, 0, 1, 2)
        settings_layout.addWidget(startup_group)
        mem_group = QGroupBox('内存分配')
        mem_group.setStyleSheet(self._group_style())
        mem_layout = QVBoxLayout(mem_group)
        self.mem_mode_group = QButtonGroup()
        self.custom_radio = QRadioButton('自定义')
        self.global_radio = QRadioButton('跟随全局')
        self.custom_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        self.global_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        self.mem_mode_group.addButton(self.custom_radio)
        self.mem_mode_group.addButton(self.global_radio)
        self.custom_radio.toggled.connect(self._on_mem_mode_toggled)
        self.global_radio.toggled.connect(self._on_mem_mode_toggled)
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(self.custom_radio)
        mode_layout.addWidget(self.global_radio)
        mode_layout.addStretch(1)
        mem_layout.addLayout(mode_layout)
        slider_layout = QHBoxLayout()
        self.mem_slider = QSlider(Qt.Horizontal)
        self.mem_slider.setRange(512, 32768)
        self.mem_slider.setTickInterval(512)
        self.mem_slider.setTickPosition(QSlider.TicksBelow)
        self.mem_slider.setStyleSheet('\n            QSlider::groove:horizontal {\n                background: #444;\n                height: 8px;\n                border-radius: 4px;\n            }\n            QSlider::handle:horizontal {\n                background: #0078d4;\n                width: 18px;\n                margin: -5px 0;\n                border-radius: 9px;\n            }\n        ')
        self.mem_slider.valueChanged.connect(self._on_mem_slider_changed)
        self.mem_slider.setEnabled(True)
        self.mem_value_label = QLabel('2G')
        self.mem_value_label.setFixedWidth(60)
        self.mem_value_label.setStyleSheet('color: #f0f0f0; font-weight: bold;')
        slider_layout.addWidget(self.mem_slider, 1)
        slider_layout.addWidget(self.mem_value_label)
        mem_layout.addLayout(slider_layout)
        settings_layout.addWidget(mem_group)
        settings_layout.addWidget(server_group)
        adv_group = QGroupBox('高级选项')
        adv_group.setStyleSheet(self._group_style())
        adv_layout = QVBoxLayout(adv_group)
        self.advanced_panel = AdvancedOptionsPanel(self._get_advanced_value, self._set_advanced_value, show_java=False)
        adv_layout.addWidget(self.advanced_panel)
        adv_note = QLabel('留空的参数会继承全局设置；这些选项仅作用于当前版本。')
        adv_note.setWordWrap(True)
        adv_note.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
        adv_layout.addWidget(adv_note)
        settings_layout.addWidget(adv_group)
        folder_group = QGroupBox('快捷方式')
        folder_group.setStyleSheet(self._group_style())
        folder_layout = QVBoxLayout(folder_group)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        self.version_folder_btn = QPushButton('版本文件夹')
        self.version_folder_btn.clicked.connect(self._open_version_folder)
        self.version_folder_btn.setStyleSheet(self._folder_btn_style())
        self.saves_folder_btn = QPushButton('存档文件夹')
        self.saves_folder_btn.clicked.connect(self._open_saves_folder)
        self.saves_folder_btn.setStyleSheet(self._folder_btn_style())
        self.mods_folder_btn = QPushButton('Mod文件夹')
        self.mods_folder_btn.clicked.connect(self._open_mods_folder)
        self.mods_folder_btn.setStyleSheet(self._folder_btn_style())
        self.screenshots_folder_btn = QPushButton('截图文件夹')
        self.screenshots_folder_btn.clicked.connect(self._open_screenshots_folder)
        self.screenshots_folder_btn.setStyleSheet(self._folder_btn_style())
        btn_layout.addWidget(self.version_folder_btn)
        btn_layout.addWidget(self.saves_folder_btn)
        btn_layout.addWidget(self.mods_folder_btn)
        btn_layout.addWidget(self.screenshots_folder_btn)
        btn_layout.addStretch(1)
        folder_layout.addLayout(btn_layout)
        overview_layout.addWidget(folder_group)
        repair_group = QGroupBox('高级管理')
        repair_group.setStyleSheet(self._group_style())
        repair_layout = QVBoxLayout(repair_group)
        repair_btn_row = QHBoxLayout()
        self.repair_btn = QPushButton('游戏修复')
        self.repair_btn.clicked.connect(self._on_repair_clicked)
        self.repair_btn.setStyleSheet(self._folder_btn_style())
        self.repair_cancel_btn = QPushButton('取消')
        self.repair_cancel_btn.clicked.connect(self._on_repair_cancel)
        self.repair_cancel_btn.setEnabled(False)
        self.repair_cancel_btn.setStyleSheet(self._folder_btn_style())
        self.export_script_btn = QPushButton('导出启动脚本')
        self.export_script_btn.clicked.connect(self._on_export_launch_script)
        self.export_script_btn.setStyleSheet(self._folder_btn_style())
        repair_btn_row.addWidget(self.repair_btn)
        repair_btn_row.addWidget(self.export_script_btn)
        repair_btn_row.addWidget(self.repair_cancel_btn)
        repair_btn_row.addStretch(1)
        repair_layout.addLayout(repair_btn_row)
        self.repair_progress = SmoothProgressBar()
        self.repair_progress.setRange(0, 100)
        self.repair_progress.reset_value()
        self.repair_progress.hide()
        self.repair_progress.setStyleSheet('\n            QProgressBar { background: #333; border-radius: 4px; height: 14px; }\n            QProgressBar::chunk { background: #0078d4; border-radius: 4px; }\n        ')
        repair_layout.addWidget(self.repair_progress)
        self.repair_status_label = QLabel('')
        self.repair_status_label.setWordWrap(True)
        self.repair_status_label.setStyleSheet('color: rgba(255,255,255,0.55); font-size: 12px;')
        repair_layout.addWidget(self.repair_status_label)
        overview_layout.addWidget(repair_group)
        overview_layout.addStretch(1)
        settings_btn_row = QHBoxLayout()
        settings_btn_row.addStretch(1)
        self.global_settings_btn = QPushButton('→ 设置')
        self.global_settings_btn.setMinimumHeight(40)
        self.global_settings_btn.setCursor(Qt.PointingHandCursor)
        self.global_settings_btn.setStyleSheet(
            'QPushButton { background:#0078d4; color:white; border:none;'
            ' border-radius:8px; padding:8px 32px; font-size:13px; font-weight:600; }'
            'QPushButton:hover { background:#1084e0; }'
            'QPushButton:pressed { background:#006cbd; }'
        )
        self.global_settings_btn.clicked.connect(self.global_settings_requested.emit)
        settings_btn_row.addWidget(self.global_settings_btn)
        settings_btn_row.addStretch(1)
        settings_layout.addLayout(settings_btn_row)
        settings_layout.addStretch(1)
        tab_host = QWidget()
        tab_host_layout = QVBoxLayout(tab_host)
        tab_host_layout.setContentsMargins(40, 0, 40, 0)
        tab_host_layout.setSpacing(0)
        tab_host_layout.addWidget(self.tabs)
        main_layout.addWidget(tab_host, 1)

    def _group_style(self):
        return '\n            QGroupBox {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.22);\n                border-radius: 12px;\n                margin-top: 0px;\n                padding-top: 8px;\n                color: rgba(255,255,255,0.85);\n            }\n            QGroupBox::title {\n                subcontrol-origin: margin;\n                left: 18px;\n                padding: 0 4px;\n                color: #f0f0f0;\n            }\n        '

    def _combo_style(self):
        return '\n            QComboBox {\n                background: rgba(255,255,255,0.05);\n                border: 1px solid rgba(255,255,255,0.1);\n                border-radius: 6px;\n                color: #f0f0f0;\n                padding: 4px 8px;\n            }\n            QComboBox::drop-down { border: none; }\n            QComboBox QAbstractItemView { background: #2a2a2a; color: #f0f0f0; border: 1px solid rgba(255,255,255,0.1); }\n        '

    def _lineedit_style(self):
        return '\n            QLineEdit {\n                background: rgba(255,255,255,0.05);\n                border: 1px solid rgba(255,255,255,0.1);\n                border-radius: 6px;\n                color: #f0f0f0;\n                padding: 4px 8px;\n            }\n        '

    def _folder_btn_style(self):
        return '\n            QPushButton {\n                background: #3a3a3a;\n                color: white;\n                border: none;\n                border-radius: 6px;\n                padding: 6px 16px;\n            }\n            QPushButton:hover { background: #4a4a4a; }\n            QPushButton:pressed { background: #2a2a2a; }\n        '

    def _parse_mem(self, mem_str):
        mem_str = mem_str.strip().upper()
        if mem_str.endswith('G'):
            return int(float(mem_str[:-1]) * 1024)
        if mem_str.endswith('M'):
            return int(float(mem_str[:-1]))
        try:
            return int(float(mem_str) * 1024)
        except:
            pass
        return 2048

    def _format_mem(self, mb):
        if mb >= 1024:
            return f'{mb / 1024:.1f}G'
        return f'{mb}M'

    def _update_mem_label(self):
        val = self.mem_slider.value()
        self.mem_value_label.setText(self._format_mem(val))

    def _save_setting(self, key, value):
        self.settings[key] = value
        self.launch_mgr.save_version_settings(self.version_id, self.settings)

    def _get_advanced_value(self, key, default=None):
        value = self.settings.get(key)
        if value is None or value == '':
            value = self.config.get(key, default)
        if value is None:
            return default
        return value

    def _set_advanced_value(self, key, value):
        self._save_setting(key, value)
        if key == 'java_path':
            idx = self.java_combo.findData(value)
            if idx < 0 and value:
                self.java_combo.addItem(f'自定义: {value}', value)
                idx = self.java_combo.count() - 1
            self.java_combo.blockSignals(True)
            self.java_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.java_combo.blockSignals(False)

    def _on_login_mode_changed(self, index):
        if index < 0:
            return
        mode = self.login_mode_combo.currentData() or 'global'
        self._save_setting('login_mode', mode)
        if hasattr(self, 'version_meta_changed'):
            self.version_meta_changed.emit(self.version_id)

    def _on_tab_changed(self, index):
        widget = self.tabs.widget(index)
        if widget is self.export_tab:
            if self._export_tab_ready:
                return
            self._export_tab_ready = True
            self._build_export_tab()
            return
        if widget is self.mod_tab:
            if self._mod_tab_ready:
                return
            self._mod_tab_ready = True
            try:
                loader = detect_installed_loader(
                    self.config.get_mc_dir(), self.version_id
                )
            except Exception:
                loader = ''
            self._installed_loader_kind = loader
            if loader in ('fabric', 'forge', 'neoforge', 'quilt'):
                from ui.pages.mod_manager_dialog import ModManagerPage
                self._mod_manager_page = ModManagerPage(
                    self.version_id, self.mod_tab, embedded=True
                )
                self.mod_tab_layout.addWidget(self._mod_manager_page)
            else:
                empty = QWidget()
                empty_layout = QVBoxLayout(empty)
                empty_layout.setContentsMargins(20, 20, 20, 20)
                empty_layout.setSpacing(14)
                empty_layout.addStretch(1)
                empty_label = QLabel('当前版本暂无Mod的加载器')
                empty_label.setAlignment(Qt.AlignCenter)
                empty_label.setFont(ui_font(20, QFont.DemiBold))
                empty_label.setStyleSheet(
                    'color:rgba(255,255,255,0.80); background:transparent;'
                )
                empty_layout.addWidget(empty_label, 0, Qt.AlignHCenter)
                install_btn = QPushButton('前往安装')
                install_btn.setMinimumHeight(44)
                install_btn.setMinimumWidth(154)
                install_btn.setCursor(Qt.PointingHandCursor)
                install_btn.setStyleSheet(
                    'QPushButton { background:#0078d4; color:white; border:none;'
                    ' border-radius:8px; padding:10px 36px; font-size:14px;'
                    ' font-weight:600; }'
                    'QPushButton:hover { background:#1084e0; }'
                    'QPushButton:pressed { background:#006cbd; }'
                )
                install_btn.clicked.connect(
                    (lambda: self.tabs.setCurrentWidget(self.modify_tab))
                )
                empty_layout.addWidget(install_btn, 0, Qt.AlignHCenter)
                empty_layout.addStretch(1)
                self.mod_tab_layout.addWidget(empty)
            return
        if widget is self.modify_tab and self._loader_modify_page is None:
            from ui.pages.loader_modify_page import LoaderModifyPage
            self._loader_modify_page = LoaderModifyPage(self.version_id, self.modify_tab)
            self._loader_modify_page.changed.connect(self._on_loader_changed)
            self.modify_tab_layout.addWidget(self._loader_modify_page)

    # ---------- 导出整合包 ----------
    _EXPORT_CARD_STYLE = (
        'QFrame#ExportCard { background: rgba(18,20,26,0.58);'
        ' border: 1px solid rgba(255,255,255,0.22); border-radius: 12px; }'
        'QFrame#ExportCard QLabel { background: transparent; border: none; }'
        'QWidget#ExportChildren { background: transparent; }'
    )

    def _build_export_tab(self):
        from core.pack_export import detect_version_info
        self._export_game_dir = self.launch_mgr.get_game_dir(self.version_id)
        try:
            self._export_info = detect_version_info(
                self.config.get_mc_dir(), self.version_id
            )
        except Exception:
            self._export_info = {'base_mc': self.version_id, 'loader': '', 'loader_version': ''}

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet('QScrollArea { background:transparent; border:none; }')
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 6, 24, 16)
        content_layout.setSpacing(14)

        # 顶部：整合包名称 / 版本
        top = QFrame()
        top.setObjectName('ExportCard')
        top.setAttribute(Qt.WA_StyledBackground, True)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(20, 14, 20, 14)
        top_layout.setSpacing(10)
        name_title = QLabel('整合包名称')
        name_title.setStyleSheet('color:rgba(255,255,255,0.85); font-size:13px;')
        top_layout.addWidget(name_title)
        default_name = display_name(self.config, self.version_id)
        self._export_name_edit = QLineEdit(default_name)
        self._export_name_edit.setPlaceholderText(default_name)
        self._export_name_edit.setFixedHeight(34)
        self._export_name_edit.setStyleSheet(
            'QLineEdit { background:rgba(255,255,255,0.06); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.2); border-radius:7px; padding:4px 10px; }'
            'QLineEdit:focus { border-color:rgba(0,120,212,0.75); }'
        )
        top_layout.addWidget(self._export_name_edit, 1)
        ver_title = QLabel('整合包版本')
        ver_title.setStyleSheet('color:rgba(255,255,255,0.85); font-size:13px;')
        top_layout.addWidget(ver_title)
        self._export_ver_edit = QLineEdit('1.0.0')
        self._export_ver_edit.setPlaceholderText('1.0.0')
        self._export_ver_edit.setFixedWidth(140)
        self._export_ver_edit.setFixedHeight(34)
        self._export_ver_edit.setStyleSheet(self._export_name_edit.styleSheet())
        top_layout.addWidget(self._export_ver_edit)
        content_layout.addWidget(top)

        # 导出内容列表
        list_card = QFrame()
        list_card.setObjectName('ExportCard')
        list_card.setAttribute(Qt.WA_StyledBackground, True)
        list_card.setStyleSheet(self._EXPORT_CARD_STYLE)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(20, 14, 20, 16)
        list_layout.setSpacing(4)
        list_title = QLabel('导出内容列表')
        list_title.setStyleSheet('color:rgba(255,255,255,0.85); font-size:14px; font-weight:600;')
        list_layout.addWidget(list_title)
        list_layout.addSpacing(4)

        loader_desc = '正式版 ' + str(self._export_info.get('base_mc') or '')
        loader_kind = str(self._export_info.get('loader') or '')
        if loader_kind:
            loader_desc += ', ' + LOADER_LABELS.get(loader_kind, loader_kind)
            loader_ver = str(self._export_info.get('loader_version') or '')
            if loader_ver:
                loader_desc += ' ' + loader_ver
        game_box, self._export_game_check = self._export_row(
            '游戏本体', loader_desc, checked=True, enabled=False)
        list_layout.addWidget(game_box)
        settings_row, self._export_settings_check = self._export_row(
            '游戏本体设置', '键位、音量、视频设置等', checked=True, indent=1)
        list_layout.addWidget(settings_row)
        personal_row, self._export_personal_check = self._export_row(
            '游戏本体个人信息', '命令历史、已保存的快捷栏', checked=False, indent=1)
        list_layout.addWidget(personal_row)

        mod_box, self._export_mods_check = self._export_row('Mod', '模组', checked=True)
        list_layout.addWidget(mod_box)
        modcfg_row, self._export_modcfg_check = self._export_row(
            'Mod 设置', '配置文件（config 目录）', checked=True, indent=1)
        list_layout.addWidget(modcfg_row)
        self._export_modcfg_check.setEnabled(True)

        self._export_rp_check = self._add_export_file_group(
            list_layout, '资源包', '纹理包/材质包',
            self._list_dir_files('resourcepacks'), default_checked=True)
        self._export_sp_check = self._add_export_file_group(
            list_layout, '光影包', '光影文件',
            self._list_dir_files('shaderpacks'), default_checked=True)

        shot_row, self._export_shots_check = self._export_row('截图', '', checked=False)
        list_layout.addWidget(shot_row)
        schem_row, self._export_schem_check = self._export_row(
            '导出的结构', 'schematics 文件夹', checked=False)
        list_layout.addWidget(schem_row)
        saves_row, self._export_saves_check = self._export_row(
            '单人游戏档案', '世界/地图', checked=False)
        list_layout.addWidget(saves_row)
        servers_row, self._export_servers_check = self._export_row(
            '多人游戏服务器列表', '', checked=False)
        list_layout.addWidget(servers_row)
        content_layout.addWidget(list_card)

        # 高级选项（默认折叠，箭头朝向指示状态）
        from ui.widgets.collapsible_section import CollapsibleSection
        advanced = CollapsibleSection('高级选项', expanded=False)
        adv_hint = QLabel('导出为标准 Modrinth 整合包，本启动器、PCL2 与 HMCL 均可直接导入。')
        adv_hint.setWordWrap(True)
        adv_hint.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px;')
        advanced.content_layout.addWidget(adv_hint)
        self._export_pack_res_check = QCheckBox('打包资源文件，以避免在导入时下载（库文件与资源索引）')
        self._export_pack_res_check.setStyleSheet('color:rgba(255,255,255,0.85); font-size:13px;')
        advanced.content_layout.addWidget(self._export_pack_res_check)
        self._export_modrinth_only_check = QCheckBox('仅从 Modrinth 下载 Mod（未匹配到 Modrinth 的本地 Mod 将不打包）')
        self._export_modrinth_only_check.setStyleSheet('color:rgba(255,255,255,0.85); font-size:13px;')
        advanced.content_layout.addWidget(self._export_modrinth_only_check)
        cfg_row = QHBoxLayout()
        cfg_row.setSpacing(10)
        load_cfg_btn = QPushButton('读取配置')
        save_cfg_btn = QPushButton('保存配置')
        for btn in (load_cfg_btn, save_cfg_btn):
            btn.setMinimumHeight(34)
            btn.setMinimumWidth(120)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                'QPushButton { background:rgba(255,255,255,0.08); color:#f0f0f0;'
                ' border:1px solid rgba(255,255,255,0.22); border-radius:7px; padding:5px 18px; }'
                'QPushButton:hover { background:rgba(255,255,255,0.15); }'
            )
        load_cfg_btn.clicked.connect(self._load_export_config)
        save_cfg_btn.clicked.connect(self._save_export_config)
        cfg_row.addWidget(load_cfg_btn)
        cfg_row.addWidget(save_cfg_btn)
        cfg_row.addStretch(1)
        advanced.content_layout.addLayout(cfg_row)
        content_layout.addWidget(advanced)
        content_layout.addStretch(1)
        # 底部留白：滚动到底时最后内容可避开悬浮按钮
        content_layout.addSpacing(70)
        scroll.setWidget(content)

        # 底部悬浮按钮（透明条，内容从其下穿过，不随内容滚动）
        bottom = _ExportBottomBar(self.export_tab)
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(24, 8, 24, 14)
        bottom_layout.addStretch(1)
        self._export_start_btn = QPushButton('开始导出')
        self._export_start_btn.setMinimumHeight(46)
        self._export_start_btn.setMinimumWidth(190)
        self._export_start_btn.setCursor(Qt.PointingHandCursor)
        self._export_start_btn.setStyleSheet(
            'QPushButton { background:#0078d4; color:white; border:none;'
            ' border-radius:9px; padding:10px 44px; font-size:15px; font-weight:600; }'
            'QPushButton:hover { background:#1084e0; }'
            'QPushButton:pressed { background:#006cbd; }'
            'QPushButton:disabled { background:rgba(128,128,128,0.25); color:#8a8f98; }'
        )
        self._export_start_btn.clicked.connect(self._on_export_clicked)
        bottom_layout.addWidget(self._export_start_btn)
        bottom_layout.addStretch(1)

        self.export_tab_layout.addWidget(scroll, 1)
        bottom.raise_()

    def _list_dir_files(self, folder):
        try:
            base = Path(self._export_game_dir) / folder
            return sorted(
                (p.name for p in base.iterdir() if p.is_file()),
                key=lambda s: s.lower(),
            )
        except OSError:
            return []

    def _export_row(self, text, desc='', checked=False, enabled=True, indent=0):
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(28 + indent * 30, 3, 12, 3)
        layout.setSpacing(10)
        check = QCheckBox(text)
        check.setChecked(checked)
        check.setEnabled(enabled)
        check.setStyleSheet('QCheckBox { color:rgba(255,255,255,0.9); font-size:13px; }')
        layout.addWidget(check)
        if desc:
            desc_label = QLabel(desc)
            desc_label.setStyleSheet('color:rgba(255,255,255,0.45); font-size:12px;')
            layout.addWidget(desc_label)
        layout.addStretch(1)
        return box, check

    def _add_export_file_group(self, parent_layout, title, desc, files, default_checked):
        """资源包/光影包：默认勾选，勾选时展示子文件（全部勾选），取消则隐藏；
        子项全部取消勾选时大项自动取消勾选。"""

        box, check = self._export_row(title, desc, checked=default_checked)
        parent_layout.addWidget(box)
        children = QWidget()
        children.setObjectName('ExportChildren')
        children_layout = QVBoxLayout(children)
        children_layout.setContentsMargins(0, 0, 0, 0)
        children_layout.setSpacing(0)
        child_checks = []
        if files:
            for name in files:
                _row, child = self._export_row(name, '', checked=default_checked, indent=2)
                children_layout.addWidget(_row)
                child_checks.append((name, child))
        else:
            empty = QLabel('（该目录为空）')
            empty.setStyleSheet('color:rgba(255,255,255,0.35); font-size:12px;')
            empty.setContentsMargins(88, 0, 0, 0)
            children_layout.addWidget(empty)
        children.setVisible(default_checked)

        def sync_from_children():
            if child_checks and not any(c.isChecked() for _n, c in child_checks):
                if check.isChecked():
                    check.blockSignals(True)
                    check.setChecked(False)
                    check.blockSignals(False)
                children.setVisible(False)

        def on_parent_toggled(checked):
            if checked:
                for _n, child in child_checks:
                    if not child.isChecked():
                        child.blockSignals(True)
                        child.setChecked(True)
                        child.blockSignals(False)
                children.setVisible(True)
            else:
                children.setVisible(False)

        for _name, child in child_checks:
            child.toggled.connect(sync_from_children)
        check.toggled.connect(on_parent_toggled)
        parent_layout.addWidget(children)
        self._export_children_states = getattr(self, '_export_children_states', {})
        self._export_children_states[title] = child_checks
        return check

    def _collect_export_selections(self):
        def child_names(title):
            return [name for name, child in self._export_children_states.get(title, []) if child.isChecked()]
        return {
            'game_settings': self._export_settings_check.isChecked(),
            'personal': self._export_personal_check.isChecked(),
            'mods': self._export_mods_check.isChecked(),
            'mod_config': self._export_mods_check.isChecked() and self._export_modcfg_check.isChecked(),
            'resourcepacks': child_names('资源包') if self._export_rp_check.isChecked() else None,
            'shaderpacks': child_names('光影包') if self._export_sp_check.isChecked() else None,
            'screenshots': self._export_shots_check.isChecked(),
            'schematics': self._export_schem_check.isChecked(),
            'saves': self._export_saves_check.isChecked(),
            'servers': self._export_servers_check.isChecked(),
            'pack_resources': self._export_pack_res_check.isChecked(),
        }

    def _export_config_data(self):
        return {
            'name': self._export_name_edit.text().strip(),
            'version': self._export_ver_edit.text().strip(),
            'selections': self._collect_export_selections(),
            'modrinth_only': self._export_modrinth_only_check.isChecked(),
        }

    def _apply_export_config(self, data):
        if not isinstance(data, dict):
            raise ValueError('配置文件格式不正确')
        if data.get('name'):
            self._export_name_edit.setText(str(data['name']))
        if data.get('version'):
            self._export_ver_edit.setText(str(data['version']))
        self._export_modrinth_only_check.setChecked(bool(data.get('modrinth_only')))
        selections = data.get('selections') or {}
        self._export_pack_res_check.setChecked(bool(selections.get('pack_resources')))
        self._export_settings_check.setChecked(bool(selections.get('game_settings')))
        self._export_personal_check.setChecked(bool(selections.get('personal')))
        self._export_mods_check.setChecked(bool(selections.get('mods')))
        self._export_modcfg_check.setChecked(bool(selections.get('mod_config')))
        self._export_shots_check.setChecked(bool(selections.get('screenshots')))
        self._export_schem_check.setChecked(bool(selections.get('schematics')))
        self._export_saves_check.setChecked(bool(selections.get('saves')))
        self._export_servers_check.setChecked(bool(selections.get('servers')))
        rp = selections.get('resourcepacks')
        self._export_rp_check.setChecked(rp is not None)
        if isinstance(rp, list):
            self._apply_child_checks('资源包', rp)
        sp = selections.get('shaderpacks')
        self._export_sp_check.setChecked(sp is not None)
        if isinstance(sp, list):
            self._apply_child_checks('光影包', sp)

    def _apply_child_checks(self, title, names):
        wanted = {str(n) for n in names}
        for name, child in self._export_children_states.get(title, []):
            child.setChecked(name in wanted)

    def _save_export_config(self):
        path, _filter = QFileDialog.getSaveFileName(
            self, '保存导出配置',
            str(Path(self.config.get_mc_dir()) / '导出配置.txt'),
            '文本文件 (*.txt)',
        )
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps(self._export_config_data(), ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        except OSError as exc:
            QMessageBox.warning(self, '保存失败', str(exc))
            return
        from ui.widgets.toast import show_toast
        show_toast('导出配置已保存', 'success', self)

    def _load_export_config(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, '读取导出配置',
            str(Path(self.config.get_mc_dir())),
            '文本文件 (*.txt)',
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '读取失败', f'配置文件解析失败：{exc}')
            return
        try:
            self._apply_export_config(data)
        except Exception as exc:
            QMessageBox.warning(self, '读取失败', str(exc))
            return
        from ui.widgets.toast import show_toast
        show_toast('导出配置已应用', 'success', self)

    def _on_export_clicked(self):
        if self._export_worker is not None and self._export_worker.isRunning():
            QMessageBox.information(self, '请稍候', '正在导出整合包，请等待完成。')
            return
        from core.modpack import sanitize_version_name
        name = sanitize_version_name(self._export_name_edit.text()) \
            or sanitize_version_name(display_name(self.config, self.version_id))
        pack_version = sanitize_version_name(self._export_ver_edit.text()) or '1.0.0'
        default_name = f'{name} {pack_version}.zip'
        path, _filter = QFileDialog.getSaveFileName(
            self, '选择导出位置',
            str(Path(self.config.get_mc_dir()) / default_name),
            '整合包 (*.zip)',
        )
        if not path:
            return
        if not path.lower().endswith('.zip'):
            path += '.zip'
        selections = self._collect_export_selections()
        if not any((selections.get('mods'), selections.get('game_settings'),
                    selections.get('personal'), selections.get('resourcepacks'),
                    selections.get('shaderpacks'), selections.get('screenshots'),
                    selections.get('schematics'), selections.get('saves'),
                    selections.get('servers'))):
            QMessageBox.warning(self, '无法导出', '请至少勾选一项要导出的内容。')
            return
        from core.pack_export import PackExportWorker
        import time as _time
        self._export_worker = PackExportWorker(
            self.version_id, self.config.get_mc_dir(), self._export_game_dir,
            path, name, pack_version, selections,
            modrinth_only=self._export_modrinth_only_check.isChecked(),
            summary=f'由 Spark Craft Launcher 从版本 {self.version_id} 导出',
        )
        worker = self._export_worker
        self._export_task_id = f'export-{self.version_id}-{int(_time.time() * 1000)}'
        task_id = self._export_task_id
        DownloadManager.instance().start_task(
            task_id, f'{name} 导出整合包',
            steps=['收集文件', '匹配 Modrinth', '写入压缩包'],
            cancel_callback=worker.cancel,
        )
        DownloadManager.instance().update_step(task_id, 0, state='active')
        self._export_start_btn.setEnabled(False)
        worker.status.connect(lambda text: (
            DownloadManager.instance().update_task(task_id, status=text)))
        worker.progress.connect(lambda v: self._on_export_progress(v))
        worker.finished_ok.connect(lambda p: self._on_export_ok(task_id, p))
        worker.finished_error.connect(lambda m: self._on_export_error(task_id, m))
        worker.finished.connect(lambda w=worker: self._on_export_finished(w))
        worker.start()
        win = self.window()
        if hasattr(win, 'show_download_manager_page'):
            win.show_download_manager_page()

    def _on_export_progress(self, value):
        if not self._export_task_id:
            return
        manager = DownloadManager.instance()
        task = manager.task(self._export_task_id)
        if not task:
            return
        steps = [dict(step) for step in task.get('steps', [])]
        ranges = [(0, 45), (45, 60), (60, 100)]
        for index, step in enumerate(steps):
            if index >= len(ranges):
                continue
            start, end = ranges[index]
            if value >= end:
                step['percent'] = 100.0
                step['state'] = 'done'
            elif value <= start:
                step['percent'] = 0.0
                step['state'] = 'pending'
            else:
                step['percent'] = round((value - start) * 100 / max(1, end - start), 1)
                step['state'] = 'active'
        if not any((step.get('state') == 'active' for step in steps)):
            for step in steps:
                if step.get('state') == 'pending':
                    step['state'] = 'active'
                    break
        manager.update_task(self._export_task_id, percent=float(value), steps=steps)

    def _on_export_ok(self, task_id, out_path):
        if task_id:
            DownloadManager.instance().finish_task(task_id, True, '导出完成')
        self._export_task_id = ''
        from ui.widgets.toast import show_toast
        show_toast('整合包导出完成！', 'success', self)
        QMessageBox.information(self, '导出完成', f'整合包已导出到：\n{out_path}')

    def _on_export_error(self, task_id, message):
        if task_id:
            DownloadManager.instance().finish_task(task_id, False, message)
        self._export_task_id = ''
        if '取消' in message:
            return
        QMessageBox.warning(self, '导出失败', message)

    def _on_export_finished(self, worker):
        if self._export_worker is worker:
            self._export_worker = None
        self._export_start_btn.setEnabled(True)
        try:
            worker.deleteLater()
        except RuntimeError:
            pass

    def _detect_installed_loader_kind(self):
        try:
            return detect_installed_loader(
                self.config.get_mc_dir(), self.version_id
            )
        except Exception:
            return ''

    def _on_loader_changed(self, _version_id):
        self._refresh_version_header()
        loader = self._detect_installed_loader_kind()
        if loader != self._installed_loader_kind:
            self._installed_loader_kind = loader
            self._reset_mod_tab()
        if hasattr(self, 'version_meta_changed'):
            self.version_meta_changed.emit(self.version_id)

    def _reset_mod_tab(self):
        if not self._mod_tab_ready and self._mod_manager_page is None:
            return
        page = self._mod_manager_page
        self._mod_manager_page = None
        if page is not None:
            try:
                page.shutdown()
            except Exception:
                pass
            try:
                page.setParent(None)
                page.deleteLater()
            except RuntimeError:
                pass
        while self.mod_tab_layout.count():
            item = self.mod_tab_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                try:
                    widget.setParent(None)
                    widget.deleteLater()
                except RuntimeError:
                    pass
        self._mod_tab_ready = False
        if self.tabs.currentWidget() is self.mod_tab:
            self._on_tab_changed(self.tabs.currentIndex())

    def _animate_tab_switch(self, index):
        widget = self.tabs.widget(index)
        if widget is None:
            return
        self._stop_tab_animation()
        QTimer.singleShot(0, lambda w=widget: self._fade_in_tab(w))

    def _stop_tab_animation(self):
        animation = self._tab_animation
        effect = self._tab_effect
        widget = self._tab_effect_widget
        self._tab_animation = None
        self._tab_effect = None
        self._tab_effect_widget = None
        if animation is not None:
            try:
                animation.stop()
            except RuntimeError:
                pass
        if widget is not None and effect is not None:
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass

    def _fade_in_tab(self, widget):
        if self._tab_animation is not None:
            return
        try:
            if widget is not self.tabs.currentWidget() or not widget.isVisible():
                return
        except RuntimeError:
            return
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b'opacity', self)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)

        def finish():
            if self._tab_effect is not effect:
                return
            self._tab_animation = None
            self._tab_effect = None
            self._tab_effect_widget = None
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass

        animation.finished.connect(finish)
        self._tab_animation = animation
        self._tab_effect = effect
        self._tab_effect_widget = widget
        animation.start()

    def _load_settings(self):
        mode = self.settings.get('login_mode', 'global')
        idx = self.login_mode_combo.findData(mode)
        self.login_mode_combo.blockSignals(True)
        self.login_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.login_mode_combo.blockSignals(False)
        self.auto_server_edit.setText(str(self.settings.get('auto_server') or ''))
        # 只用已就绪的扫描缓存填充；缓存未就绪时后台扫描，完成后自动刷新，
        # 绝不在 UI 线程同步做全盘 Java 扫描（会导致打开页面长时间卡死）
        java_list = self.launch_mgr.peek_cached_java_list()
        self._fill_java_combo(java_list)
        if java_list is None:
            try:
                from core.java_scan import JavaScanService
                service = JavaScanService.instance()
                if not getattr(self, '_java_scan_connected', False):
                    service.finished.connect(self._on_java_scan_refresh)
                    self._java_scan_connected = True
                service.start()
            except Exception:
                pass
        mem_mode = self.settings.get('mem_mode', 'global')
        if mem_mode == 'global':
            self.global_radio.setChecked(True)
        else:
            self.custom_radio.setChecked(True)
        mem_max = self.settings.get('memory_max', '')
        if mem_max:
            size = self._parse_mem(mem_max)
            if size:
                self.mem_slider.setValue(size)
        else:
            global_mem = self.config.get('memory_max', '4G')
            size = self._parse_mem(global_mem)
            self.mem_slider.setValue(size)
        self._update_mem_label()
        self.title_edit.setText(self.settings.get('window_title', ''))
        self.iso_combo.blockSignals(True)
        self.iso_combo.setCurrentIndex(1 if self._effective_isolated else 0)
        self.iso_combo.blockSignals(False)
        self._on_mem_mode_toggled()
        self._load_personalization()
        # 延迟到页面显示后再刷新提示，避免 java 版本探测阻塞打开过程
        QTimer.singleShot(0, self._update_java_hint)

    def _on_mem_mode_toggled(self):
        enabled = self.custom_radio.isChecked()
        self.mem_slider.setEnabled(enabled)
        if enabled:
            self._save_setting('mem_mode', 'custom')
            self._save_setting('memory_max', self._format_mem(self.mem_slider.value()))
            return
        self._save_setting('mem_mode', 'global')
        self._save_setting('memory_max', '')

    def _on_mem_slider_changed(self, value):
        self._update_mem_label()
        if self.custom_radio.isChecked():
            self._save_setting('memory_max', self._format_mem(value))

    def _on_java_changed(self, index):
        if index == 0:
            self._save_setting('java_path', '')
        else:
            path = self.java_combo.itemData(index)
            if path:
                self._save_setting('java_path', path)
        try:
            self.advanced_panel._reload_java()
        except Exception:
            pass
        self._update_java_hint()

    def _on_title_changed(self, text):
        self._save_setting('window_title', text.strip())

    def _on_iso_combo_changed(self, index):
        self._effective_isolated = bool(self.iso_combo.currentData())
        self._save_setting('isolated', self._effective_isolated)
        # 与 PCL/HMCL 互通：同步版本隔离标记
        try:
            from core.interop import write_isolation_markers
            version_dir = Path(self.config.get_mc_dir()) / 'versions' / self.version_id
            write_isolation_markers(version_dir, self._effective_isolated)
        except Exception:
            pass

    def _on_auto_server_changed(self):
        value = self.auto_server_edit.text().strip()
        if value:
            self._save_setting('auto_server', value)
        else:
            settings = dict(self.launch_mgr.get_version_settings(self.version_id) or {})
            settings.pop('auto_server', None)
            self.launch_mgr.save_version_settings(self.version_id, settings)
            self.settings = settings

    def _update_java_hint(self):
        path = str(self.java_combo.itemData(self.java_combo.currentIndex()) or '')
        worker = getattr(self, '_java_hint_worker', None)
        if worker is not None and worker.isRunning():
            return
        self.java_hint_label.setText('正在检查 Java...')
        self.java_hint_label.show()
        worker = _JavaHintWorker(self.launch_mgr, self.version_id, path)
        self._java_hint_worker = worker
        worker.done.connect(self._on_java_hint_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_java_hint_done(self, path, version):
        try:
            if not path or not os.path.isfile(path):
                self.java_hint_label.hide()
                return
            major = self.launch_mgr._parse_java_major(version)
            version_text = f'Java {major}' if major is not None else 'Java'
            if version:
                version_text += f' ({version})'
            self.java_hint_label.setText(
                f'将会使用：{version_text}  {Path(path).parent}'
            )
            self.java_hint_label.show()
        except RuntimeError:
            pass

    def _fill_java_combo(self, java_list):
        self.java_combo.clear()
        self.java_combo.addItem('自动选择', '')
        for ver, path in (java_list or []):
            self.java_combo.addItem(f'Java {ver}  [路径: {path}]', path)
        # 版本独立设置优先；没设置时沿用全局导入/选择的 Java
        current_java = str(self.settings.get('java_path') or '').strip()
        if not current_java:
            try:
                current_java = str(self.config.get('java_path') or '').strip()
            except Exception:
                current_java = ''
        if current_java:
            idx = self.java_combo.findData(current_java)
            if idx < 0:
                prefix = '自定义' if self.settings.get('java_path') else '全局'
                self.java_combo.addItem(f'{prefix}: {current_java}', current_java)
                idx = self.java_combo.count() - 1
            self.java_combo.setCurrentIndex(idx)
        else:
            self.java_combo.setCurrentIndex(0)

    def _on_java_scan_refresh(self, result):
        '''后台 Java 扫描完成后刷新下拉框（保留当前选择）。'''
        try:
            current = str(self.java_combo.currentData() or '')
            self._fill_java_combo(result)
            if current:
                idx = self.java_combo.findData(current)
                if idx >= 0:
                    self.java_combo.setCurrentIndex(idx)
            self._update_java_hint()
        except RuntimeError:
            pass

    _BUILTIN_ICON_VALUES = ('release', 'snapshot', 'fabric', 'forge', 'neoforge', 'optifine', '__custom__')

    def _prune_custom_icon_items(self, keep=''):
        '''去掉历史遗留的自定义图片项，只保留当前选中项。'''

        for index in range(self.icon_combo.count() - 1, -1, -1):
            data = self.icon_combo.itemData(index)
            if not isinstance(data, str):
                continue
            if data in self._BUILTIN_ICON_VALUES or data == keep:
                continue
            self.icon_combo.removeItem(index)

    def _load_personalization(self):
        settings = version_settings(self.config, self.version_id)
        icon_value = str(settings.get('icon') or '')
        if not icon_value or icon_value in ('auto', 'custom'):
            icon_value = default_icon_value(
                self.config.get_mc_dir(), self.version_id, self.config
            )
        self.icon_combo.blockSignals(True)
        self._prune_custom_icon_items(icon_value)
        index = self.icon_combo.findData(icon_value)
        if index < 0 and Path(icon_value).is_file():
            self.icon_combo.insertItem(self.icon_combo.count() - 1, '自定义图片', icon_value)
            index = self.icon_combo.findData(icon_value)
        self.icon_combo.setCurrentIndex(index if index >= 0 else 0)
        self.icon_combo.blockSignals(False)
        category = str(settings.get('category') or 'auto')
        self.category_combo.blockSignals(True)
        index = self.category_combo.findData(category)
        self.category_combo.setCurrentIndex(index if index >= 0 else 0)
        self.category_combo.blockSignals(False)
        favorites = self.config.get('favorited_versions', []) or []
        self.favorite_btn.setText('取消收藏' if self.version_id in favorites else '加入收藏夹')
        self._refresh_version_header()

    def _refresh_version_header(self):
        mc_dir = self.config.get_mc_dir()
        name = display_name(self.config, self.version_id)
        self.version_name_label.setText(name)
        self.version_desc_label.setText(
            version_description(mc_dir, self.version_id, self.config)
        )
        self.page_title_label.setText(f'游戏设置 - {name}')
        icon_path = version_icon(
            mc_dir, self.version_id, self.config
        )
        if icon_path is not None:
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                self.version_icon_label.setPixmap(
                    hidpi_pixmap(pixmap, 48, 48)
                )
                return
        self.version_icon_label.clear()

    def _save_meta(self, key, value=None, emit=True):
        settings = dict(self.launch_mgr.get_version_settings(self.version_id) or {})
        if value is None or value == '':
            settings.pop(key, None)
        else:
            settings[key] = value
        self.launch_mgr.save_version_settings(self.version_id, settings)
        self.settings = settings
        self._refresh_version_header()
        if emit and hasattr(self, 'version_meta_changed'):
            self.version_meta_changed.emit(self.version_id)

    def _on_icon_changed(self, index):
        value = self.icon_combo.currentData()
        if value == '__custom__':
            path, _ = QFileDialog.getOpenFileName(
                self, '选择版本图标', str(Path.home()),
                '图片 (*.png *.jpg *.jpeg *.ico)',
            )
            if not path:
                self._load_personalization()
                return
            self.icon_combo.blockSignals(True)
            self._prune_custom_icon_items(path)
            existing = self.icon_combo.findData(path)
            if existing < 0:
                self.icon_combo.insertItem(self.icon_combo.count() - 1, '自定义图片', path)
                existing = self.icon_combo.findData(path)
            self.icon_combo.setCurrentIndex(existing)
            self.icon_combo.blockSignals(False)
            self._save_meta('icon', path)
            return
        self._save_meta('icon', value or 'auto')

    def _on_category_changed(self, index):
        self._save_meta('category', self.category_combo.currentData() or 'auto')

    def _rename_version_display(self):
        dialog = FramelessInputDialog(
            self, '修改版本名', '显示名称（留空恢复版本 ID）：',
            display_name(self.config, self.version_id),
        )
        if not dialog.exec():
            return
        value = str(dialog.value or '').strip()
        nicknames = dict(self.config.get('version_nicknames', {}) or {})
        if value and value != self.version_id:
            nicknames[self.version_id] = value
        else:
            nicknames.pop(self.version_id, None)
        self.config.set('version_nicknames', nicknames)
        self.config.save()
        self._refresh_version_header()
        if hasattr(self, 'version_meta_changed'):
            self.version_meta_changed.emit(self.version_id)

    def _edit_version_description(self):
        settings = version_settings(self.config, self.version_id)
        current = str(settings.get('description') or '') or version_description(
            self.config.get_mc_dir(), self.version_id, self.config
        )
        dialog = FramelessInputDialog(
            self, '修改版本描述', '版本描述（留空恢复自动描述）：', current,
        )
        if not dialog.exec():
            return
        value = str(dialog.value or '').strip()
        self._save_meta('description', value or None)

    def _toggle_favorite_version(self):
        favorites = list(self.config.get('favorited_versions', []) or [])
        if self.version_id in favorites:
            favorites.remove(self.version_id)
        else:
            favorites.append(self.version_id)
        self.config.set('favorited_versions', favorites)
        self.config.save()
        self.favorite_btn.setText('取消收藏' if self.version_id in favorites else '加入收藏夹')
        if hasattr(self, 'version_meta_changed'):
            self.version_meta_changed.emit(self.version_id)

    def _get_base_dir(self):
        '''获取当前游戏目录（.minecraft）'''

        return Path(self.config.get_mc_dir())

    def _get_version_dir(self):
        '''获取版本文件夹路径（始终在 versions/<version_id> 下）'''

        base = self._get_base_dir()
        return base / 'versions' / self.version_id

    def _get_saves_dir(self):
        '''根据隔离状态获取存档文件夹路径'''

        base = self._get_base_dir()
        if self._effective_isolated:
            return self._get_version_dir() / 'saves'
        return base / 'saves'

    def _get_mods_dir(self):
        '''根据隔离状态获取Mod文件夹路径'''

        base = self._get_base_dir()
        if self._effective_isolated:
            return self._get_version_dir() / 'mods'
        return base / 'mods'

    def _get_screenshots_dir(self):
        '''根据隔离状态获取截图文件夹路径'''

        base = self._get_base_dir()
        if self._effective_isolated:
            return self._get_version_dir() / 'screenshots'
        return base / 'screenshots'

    def _ensure_and_open_folder(self, path: Path, label: str):
        '''确保文件夹存在并打开'''

        try:
            path.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                import subprocess
                import sys
                if sys.platform == 'win32':
                    os.startfile(str(path))
                    return
                if sys.platform == 'darwin':
                    subprocess.run(['open', str(path)])
                    return
                subprocess.run(['xdg-open', str(path)])
            return
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, '打开失败', f'无法打开 {label} 文件夹：{str(e)}')
            return

    def _open_version_folder(self):
        path = self._get_version_dir()
        self._ensure_and_open_folder(path, '版本')

    def _open_saves_folder(self):
        path = self._get_saves_dir()
        self._ensure_and_open_folder(path, '存档')

    def _open_mods_folder(self):
        path = self._get_mods_dir()
        self._ensure_and_open_folder(path, 'Mod')

    def _open_screenshots_folder(self):
        path = self._get_screenshots_dir()
        self._ensure_and_open_folder(path, '截图')

    def _on_export_launch_script(self):
        script, error = self.launch_mgr.build_launch_script(self.version_id)
        if not script:
            QMessageBox.critical(self, '导出失败', error or '无法生成启动命令')
            return
        folder = QFileDialog.getExistingDirectory(self, '选择启动脚本导出位置', str(Path.home()))
        if not folder:
            return
        safe_name = re.sub('[<>:"/\\\\|?*\\x00-\\x1f]', '_', self.version_id).strip() or '版本'
        path = Path(folder) / f'导出{safe_name}.bat'
        if path.exists():
            reply = QMessageBox.question(self, '文件已存在', f'{path.name} 已存在，是否覆盖？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        try:
            with open(path, 'w', encoding='gbk', errors='replace', newline='') as fh:
                fh.write(script)
        except OSError as exc:
            QMessageBox.critical(self, '导出失败', str(exc))
            return
        QMessageBox.information(self, '导出成功', f'启动脚本已导出到：\n{path}')

    def _on_repair_clicked(self):
        if self._repair_worker and self._repair_worker.isRunning():
            return
        reply = QMessageBox.question(self, '游戏修复', f'将校验并补全版本「{self.version_id}」缺失或损坏的文件。\n需要联网下载，可能耗时几分钟，是否继续？', QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return
        self.repair_btn.setEnabled(False)
        self.repair_cancel_btn.setEnabled(True)
        self.repair_progress.reset_value()
        self.repair_progress.show()
        self.repair_status_label.setText('正在准备修复...')
        self.repair_status_label.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 12px;')
        import time as _time
        self._repair_task_id = f'repair-{self.version_id}-{int(_time.time() * 1000)}'
        DownloadManager.instance().start_task(self._repair_task_id, f'{self.version_id} 修复', steps=['校验版本文件', '补全缺失文件', '解压 natives'], cancel_callback=(lambda: None))
        DownloadManager.instance().update_step(self._repair_task_id, 0, state='active')
        self._repair_worker = GameRepairWorker(self.version_id, self.config.get_mc_dir(), self)
        self._repair_worker.status_updated.connect(self._on_repair_status)
        self._repair_worker.progress_updated.connect(self._on_repair_progress)
        self._repair_worker.log_message.connect((lambda s: print(f'[修复] {s}')))
        self._repair_worker.finished_ok.connect(self._on_repair_ok)
        self._repair_worker.finished_error.connect(self._on_repair_error)
        self._repair_worker.finished.connect(self._on_repair_finished)
        self._repair_worker.start()

    def _on_repair_cancel(self):
        if self._repair_worker and self._repair_worker.isRunning():
            self._repair_worker.cancel()
            self.repair_cancel_btn.setEnabled(False)
            self.repair_status_label.setText('正在取消修复...')

    def _on_repair_status(self, text):
        self.repair_status_label.setText(text)
        if self._repair_task_id:
            DownloadManager.instance().update_task(self._repair_task_id, status=text)

    def _on_repair_progress(self, value):
        self.repair_progress.set_animated_value(value)
        if not self._repair_task_id:
            return
        manager = DownloadManager.instance()
        task = manager.task(self._repair_task_id)
        if not task:
            return
        steps = [dict(step) for step in task.get('steps', [])]
        ranges = [(0, 20), (20, 90), (90, 100)]
        for index, step in enumerate(steps):
            if index >= len(ranges):
                continue
            start, end = ranges[index]
            if value >= end:
                step['percent'] = 100.0
                step['state'] = 'done'
            elif value <= start:
                step['percent'] = 0.0
                step['state'] = 'pending'
            else:
                step['percent'] = round((value - start) * 100 / max(1, end - start), 1)
                step['state'] = 'active'
        if not any((step.get('state') == 'active' for step in steps)):
            for step in steps:
                if step.get('state') == 'pending':
                    step['state'] = 'active'
                    break
        manager.update_task(self._repair_task_id, percent=float(value), steps=steps)

    def _on_repair_ok(self, message):
        if self._repair_task_id:
            DownloadManager.instance().finish_task(self._repair_task_id, True, '修复完成')
        try:
            ok, reason = self.launch_mgr.pre_launch_check(self.version_id)
        except Exception as exc:
            ok, reason = False, str(exc)
        if not ok:
            message += f'\n\n文件修复已完成，但启动前检查未通过：{reason}'
        self.repair_status_label.setText(message)
        self.repair_status_label.setStyleSheet('color: #4ade80; font-size: 12px;' if ok else 'color: #f0a030; font-size: 12px;')
        QMessageBox.information(self, '修复完成', message)

    def _on_repair_error(self, message):
        if self._repair_task_id:
            DownloadManager.instance().finish_task(self._repair_task_id, False, message)
        self.repair_status_label.setText(message)
        self.repair_status_label.setStyleSheet('color: #ff7777; font-size: 12px;')
        if '取消' not in message:
            QMessageBox.warning(self, '修复失败', message)

    def _on_repair_finished(self):
        self.repair_btn.setEnabled(True)
        self.repair_cancel_btn.setEnabled(False)
        if self._repair_worker:
            self._repair_worker.deleteLater()
            self._repair_worker = None

    def has_active_repair(self) -> bool:
        worker = self._repair_worker
        try:
            if worker is not None:
                if worker.isRunning() and not getattr(worker, '_cancelled', False):
                    return True
                return has_active_repairs()
        except RuntimeError:
            pass
        return has_active_repairs()

    def shutdown(self):
        self._stop_tab_animation()
        worker = self._export_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            if not worker.wait(2500):
                worker.setParent(None)
        worker = self._repair_worker
        if worker and worker.isRunning():
            worker.cancel()
            if not worker.wait(2500):
                _DETACHED_REPAIR_WORKERS.add(worker)
                worker.setParent(None)
                worker.finished.connect((lambda w=worker: _DETACHED_REPAIR_WORKERS.discard(w)))
        for page in (self._mod_manager_page, self._loader_modify_page):
            if page is not None:
                try:
                    page.shutdown()
                except Exception:
                    pass

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def refresh_settings(self):
        self._load_settings()


# WARNING: Decompyle incomplete
