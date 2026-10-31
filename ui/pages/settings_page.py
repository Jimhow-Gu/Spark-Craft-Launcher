'''
设置页面 - 游戏目录、Java列表、内存（自定义/自动配置）、镜像等
所有更改自动保存
'''

import os
import subprocess
import re
import sys
import webbrowser
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QGroupBox, QFileDialog, QScrollArea, QListWidget, QListWidgetItem, QMessageBox, QApplication, QRadioButton, QButtonGroup, QSlider, QCheckBox, QLineEdit, QSpinBox, QTabWidget, QGraphicsOpacityEffect, QProgressBar)
from ui.widgets.themed_checkbox import ThemedCheckBox
from ui.widgets.persistent_combo import PersistentComboBox
from PySide6.QtCore import (Qt, Signal, QUrl, QTimer, QEasingCurve, QPropertyAnimation)
from PySide6.QtGui import (QFont, QDesktopServices, QColor, QPainter, QPalette, QPainterPath, QPixmap)
from PySide6.QtCore import QRectF
from core.app_font import ui_font
from core.config import ConfigManager
from core.launch import LaunchManager
from core.java_scan import JavaScanService
from core.logging_setup import LOG_DIR, clear_logs
from core.background import (FIT_OPTIONS, LOOP_MODES, DEFAULT_LOOP_MODE, get_settings as get_background_settings, list_background_images, next_image, pictures_dir, save_setting as save_background_setting)
from core.music import (BackgroundMusicPlayer, list_music_files, music_dir)
from ui.widgets.advanced_options import AdvancedOptionsPanel
from ui.widgets.dark_dialog import DarkQuestionDialog
from ui.widgets.form_styles import RADIO_BUTTON_STYLE
from ui.widgets.painted_icons import painted_icon
from ui.widgets.collapsible_section import CollapsibleSection
from ui.widgets.offline_skin_settings import OfflineSkinSettings
from ui.widgets.settings_widgets import MemoryUsageBar

class SettingsPage(QWidget):
    settings_changed = Signal()
    background_changed = Signal()
    background_refresh = Signal()
    title_changed = Signal(str)
    theme_changed = Signal(str)
    video_music_changed = Signal(bool)
    video_volume_changed = Signal(int)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('SettingsPage')
        self.config = ConfigManager()
        self.launch_mgr = LaunchManager()
        self._cached_java_list = []
        self._settings_tab_animation = None
        self._settings_tab_effect = None
        self._settings_tab_effect_widget = None
        self._init_ui()
        self._bg_timer = QTimer(self)
        self._bg_timer.setSingleShot(True)
        self._bg_timer.setInterval(140)
        self._bg_timer.timeout.connect(self._apply_background_settings)
        self._bg_preview_timer = QTimer(self)
        self._bg_preview_timer.setSingleShot(True)
        self._bg_preview_timer.setInterval(40)
        self._bg_preview_timer.timeout.connect(self._preview_background_settings)
        self._title_timer = QTimer(self)
        self._title_timer.setSingleShot(True)
        self._title_timer.setInterval(150)
        self._title_timer.timeout.connect(self._apply_window_title)
        self._download_save_timer = QTimer(self)
        self._download_save_timer.setSingleShot(True)
        self._download_save_timer.setInterval(250)
        self._download_save_timer.timeout.connect(self.config.save)
        self._mem_live_timer = QTimer(self)
        self._mem_live_timer.setInterval(2000)
        self._mem_live_timer.timeout.connect(self._update_live_memory)
        self._mem_live_timer.start()
        self._manual_java_detect = False
        self._last_iso_value = 'off'
        self.java_scan = JavaScanService.instance()
        self.java_scan.finished.connect(self._on_java_scan_finished)
        self._load_settings()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet('\n            QScrollArea { background: transparent; border: none; }\n            QScrollBar:vertical { background: #2a2a2a; width: 12px; border-radius: 6px; }\n            QScrollBar::handle:vertical { background: #555; border-radius: 6px; min-height: 20px; }\n            QScrollBar::handle:vertical:hover { background: #777; }\n            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }\n        ')
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        title = QLabel('设置')
        title.setFont(ui_font(24, QFont.DemiBold))
        title.setStyleSheet('color: #f0f0f0;')
        layout.addWidget(title)
        theme_group = self._create_group('主题')
        theme_layout = theme_group.layout()
        theme_row = QHBoxLayout()
        theme_label = QLabel('界面主题')
        theme_label.setFixedWidth(90)
        theme_label.setStyleSheet('color: rgba(255,255,255,0.7);')
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_buttons = {}
        theme_button_style = (
            'QPushButton { background:rgba(255,255,255,0.06); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.14); border-radius:6px;'
            ' padding:6px 16px; }'
            'QPushButton:hover { background:rgba(255,255,255,0.12); }'
            'QPushButton:checked { background:#0078d4; color:white;'
            ' border-color:#0078d4; }'
        )
        theme_row.addWidget(theme_label)
        for key, label in (
            ('dark', '深色模式'),
            ('light', '浅色模式'),
            ('system', '跟随系统'),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(theme_button_style)
            button.clicked.connect(
                lambda checked, value=key: self._on_theme_selected(value)
            )
            self.theme_group.addButton(button)
            self.theme_buttons[key] = button
            theme_row.addWidget(button)
        theme_row.addStretch(1)
        theme_layout.addLayout(theme_row)
        layout.addWidget(theme_group)
        java_group = self._create_group('Java 列表')
        java_group_layout = java_group.layout()
        java_row = QHBoxLayout()
        self.java_combo = QComboBox()
        self.java_combo.setMinimumHeight(36)
        self.java_combo.setPlaceholderText('请点击「检测Java」扫描系统')
        self.java_combo.setStyleSheet('\n            QComboBox {\n                background: rgba(255,255,255,0.05);\n                border: 1px solid rgba(255,255,255,0.1);\n                border-radius: 6px;\n                color: #f0f0f0;\n                padding: 4px 8px;\n            }\n            QComboBox::drop-down { border: none; }\n            QComboBox QAbstractItemView { background: #2a2a2a; color: #f0f0f0; border: 1px solid rgba(255,255,255,0.1); }\n        ')
        self.java_combo.currentIndexChanged.connect(self._on_java_selected)
        java_row.addWidget(self.java_combo, 1)
        detect_btn = QPushButton('检测Java')
        detect_btn.clicked.connect(self._on_detect_java)
        detect_btn.setStyleSheet('QPushButton { background: #3a3a3a; color: white; border: none; border-radius: 6px; padding: 6px 12px; } QPushButton:hover { background: #4a4a4a; }')
        import_btn = QPushButton('导入')
        import_btn.clicked.connect(self._on_import_java)
        import_btn.setStyleSheet('QPushButton { background: #3a3a3a; color: white; border: none; border-radius: 6px; padding: 6px 16px; } QPushButton:hover { background: #4a4a4a; }')
        java_row.addWidget(detect_btn)
        java_row.addWidget(import_btn)
        java_group_layout.addLayout(java_row)
        self.java_status = QLabel('')
        self.java_status.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
        self.java_status.setWordWrap(True)
        java_group_layout.addWidget(self.java_status)
        layout.addWidget(java_group)
        mem_group = self._create_group('内存分配')
        mem_group_layout = mem_group.layout()
        self.mem_mode_group = QButtonGroup()
        self.custom_radio = QRadioButton('自定义')
        self.auto_radio = QRadioButton('自动配置')
        self.custom_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        self.auto_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        self.mem_mode_group.addButton(self.custom_radio)
        self.mem_mode_group.addButton(self.auto_radio)
        self.custom_radio.toggled.connect(self._on_mem_mode_toggled)
        self.auto_radio.toggled.connect(self._on_mem_mode_toggled)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.custom_radio)
        mode_row.addWidget(self.auto_radio)
        mode_row.addStretch(1)
        mem_group_layout.addLayout(mode_row)
        self.memory_optimize_check = ThemedCheckBox('启动游戏前进行内存优化')
        self.memory_optimize_check.toggled.connect(self._on_memory_optimize_toggled)
        mem_group_layout.addWidget(self.memory_optimize_check)
        slider_row = QHBoxLayout()
        self.mem_slider = QSlider(Qt.Horizontal)
        self.mem_slider.setRange(512, 32768)
        self.mem_slider.setTickInterval(512)
        self.mem_slider.setTickPosition(QSlider.TicksBelow)
        self.mem_slider.setStyleSheet('\n            QSlider::groove:horizontal {\n                background: #444;\n                height: 8px;\n                border-radius: 4px;\n            }\n            QSlider::handle:horizontal {\n                background: #0078d4;\n                width: 18px;\n                margin: -5px 0;\n                border-radius: 9px;\n            }\n        ')
        self.mem_slider.valueChanged.connect(self._on_mem_slider_changed)
        self.mem_value_label = QLabel('2G')
        self.mem_value_label.setFixedWidth(60)
        self.mem_value_label.setStyleSheet('color: #f0f0f0; font-weight:900;')
        slider_row.addWidget(self.mem_slider, 1)
        slider_row.addWidget(self.mem_value_label)
        mem_group_layout.addLayout(slider_row)
        self.auto_info_label = QLabel('')
        self.auto_info_label.setStyleSheet('color: #4ade80; font-size: 12px;')
        mem_group_layout.addWidget(self.auto_info_label)
        mem_labels_row = QHBoxLayout()
        self.memory_usage_label = QLabel('已使用内存 -- / --')
        self.memory_usage_label.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px;')
        mem_labels_row.addWidget(self.memory_usage_label)
        mem_labels_row.addStretch(1)
        self.memory_alloc_label = QLabel('游戏分配 --')
        self.memory_alloc_label.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px;')
        mem_labels_row.addWidget(self.memory_alloc_label)
        mem_group_layout.addLayout(mem_labels_row)
        self.memory_usage_bar = MemoryUsageBar()
        mem_group_layout.addWidget(self.memory_usage_bar)
        layout.addWidget(mem_group)
        thread_group = self._create_group('下载线程数')
        thread_layout = thread_group.layout()
        thread_tip = QLabel('控制同时下载的最大任务数，默认 64，最高 256。线程数越高下载越快，但可能造成明显的网络与设备卡顿。')
        thread_tip.setWordWrap(True)
        thread_tip.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 12px;')
        thread_layout.addWidget(thread_tip)
        thread_row = QHBoxLayout()
        self.thread_slider = QSlider(Qt.Horizontal)
        self.thread_slider.setRange(1, 256)
        self.thread_slider.setValue(64)
        self.thread_slider.setTickInterval(16)
        self.thread_slider.setTickPosition(QSlider.TicksBelow)
        self.thread_slider.setStyleSheet('\n            QSlider::groove:horizontal {\n                background: #444; height: 8px; border-radius: 4px;\n            }\n            QSlider::handle:horizontal {\n                background: #0078d4; width: 18px; margin: -5px 0;\n                border-radius: 9px;\n            }\n        ')
        self.thread_value_label = QLabel('64')
        self.thread_value_label.setFixedWidth(50)
        self.thread_value_label.setStyleSheet('color: #f0f0f0; font-weight:900;')
        self.thread_slider.valueChanged.connect(self._on_threads_changed)
        thread_row.addWidget(self.thread_slider, 1)
        thread_row.addWidget(self.thread_value_label)
        thread_layout.addLayout(thread_row)
        speed_row = QHBoxLayout()
        speed_label = QLabel('速度限制')
        speed_label.setFixedWidth(70)
        speed_label.setStyleSheet('color: rgba(255,255,255,0.7);')
        self.speed_limit_slider = QSlider(Qt.Horizontal)
        self.speed_limit_slider.setRange(0, 1000)
        self.speed_limit_slider.setValue(1000)
        self.speed_limit_slider.setTickInterval(100)
        self.speed_limit_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_limit_slider.setStyleSheet(self.thread_slider.styleSheet())
        self.speed_limit_label = QLabel('无限制')
        self.speed_limit_label.setMinimumWidth(90)
        self.speed_limit_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.speed_limit_label.setStyleSheet('color:#f0f0f0; font-weight:900;')
        self.speed_limit_slider.valueChanged.connect(self._on_speed_limit_changed)
        speed_row.addWidget(speed_label)
        speed_row.addWidget(self.speed_limit_slider, 1)
        speed_row.addWidget(self.speed_limit_label)
        thread_layout.addLayout(speed_row)
        speed_tip = QLabel('最低 0.1 MB/s，向右拖动提高上限；最右侧为无限制（默认）。')
        speed_tip.setWordWrap(True)
        speed_tip.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
        thread_layout.addWidget(speed_tip)
        self._threads_warned = False
        layout.addWidget(thread_group)
        launch_group = self._create_group('启动选项')
        launch_layout = launch_group.layout()
        def add_launch_row(label, widget):
            row = QHBoxLayout()
            text = QLabel(label)
            text.setFixedWidth(120)
            text.setStyleSheet('color: rgba(255,255,255,0.72);')
            row.addWidget(text)
            row.addWidget(widget, 1)
            launch_layout.addLayout(row)
            return row

        self.default_iso_combo = QComboBox()
        for text, value in (('关闭', 'off'), ('隔离可安装 Mod 的版本', 'modded'), ('隔离非正式版', 'non_release'), ('隔离可安装 Mod 的版本与非正式版', 'modded_non_release'), ('隔离所有版本', 'all')):
            self.default_iso_combo.addItem(text, value)
        self.default_iso_combo.setStyleSheet(self.java_combo.styleSheet())
        self.default_iso_combo.currentIndexChanged.connect(self._on_default_iso_changed)
        add_launch_row('默认版本隔离', self.default_iso_combo)
        self.game_title_edit = QLineEdit()
        self.game_title_edit.setPlaceholderText('默认')
        self.game_title_edit.setStyleSheet('QLineEdit { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#f0f0f0; padding:5px 8px; }')
        self.game_title_edit.editingFinished.connect(self._save_launch_options)
        add_launch_row('游戏窗口标题', self.game_title_edit)
        self.custom_info_edit = QLineEdit()
        self.custom_info_edit.setPlaceholderText('可选，写入启动器显示的自定义信息')
        self.custom_info_edit.setStyleSheet('QLineEdit { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#f0f0f0; padding:5px 8px; }')
        self.custom_info_edit.editingFinished.connect(self._save_launch_options)
        add_launch_row('自定义信息', self.custom_info_edit)
        self.launcher_visibility_combo = QComboBox()
        for text, value in (('游戏启动后仍保持不变', 'keep'), ('游戏启动后立即关闭', 'close'), ('游戏启动后隐藏，游戏退出后自动关闭', 'hide_close'), ('游戏启动后隐藏，游戏退出后重新打开', 'hide_restore'), ('游戏启动后最小化', 'minimize')):
            self.launcher_visibility_combo.addItem(text, value)
        self.launcher_visibility_combo.setStyleSheet(self.java_combo.styleSheet())
        self.launcher_visibility_combo.currentIndexChanged.connect(self._save_launch_options)
        add_launch_row('启动器可见性', self.launcher_visibility_combo)
        self.process_priority_combo = QComboBox()
        for text, value in (('高（优先保证游戏运行，游戏性能更佳，但可能造成其他程序卡顿）', 'high'), ('中（平衡）', 'normal'), ('低（优先保证其他程序运行，但可能让游戏卡顿，适合挂机时使用）', 'low')):
            self.process_priority_combo.addItem(text, value)
        self.process_priority_combo.setStyleSheet(self.java_combo.styleSheet())
        self.process_priority_combo.currentIndexChanged.connect(self._save_launch_options)
        add_launch_row('进程优先级', self.process_priority_combo)
        self.game_window_size_combo = QComboBox()
        for text, value in (('全屏', 'fullscreen'), ('默认', 'default'), ('与启动器尺寸一致', 'launcher'), ('自定义尺寸', 'custom'), ('最大化', 'maximized')):
            self.game_window_size_combo.addItem(text, value)
        self.game_window_size_combo.setStyleSheet(self.java_combo.styleSheet())
        self.game_window_size_combo.currentIndexChanged.connect(self._on_game_window_size_changed)
        add_launch_row('窗口大小', self.game_window_size_combo)
        self.custom_size_widget = QWidget()
        custom_row = QHBoxLayout(self.custom_size_widget)
        custom_row.setContentsMargins(0, 0, 0, 0)
        self.game_width_spin = QSpinBox()
        self.game_width_spin.setRange(320, 7680)
        self.game_width_spin.setValue(854)
        self.game_height_spin = QSpinBox()
        self.game_height_spin.setRange(240, 4320)
        self.game_height_spin.setValue(480)
        for spin in self.game_width_spin, self.game_height_spin:
            spin.setStyleSheet('QSpinBox { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#f0f0f0; padding:4px 8px; }')
            spin.valueChanged.connect(self._save_launch_options)
        custom_row.addWidget(QLabel('宽'))
        custom_row.addWidget(self.game_width_spin)
        custom_row.addWidget(QLabel('高'))
        custom_row.addWidget(self.game_height_spin)
        custom_row.addStretch(1)
        self.custom_size_widget.hide()
        launch_layout.addWidget(self.custom_size_widget)
        layout.addWidget(launch_group)
        advanced_group = CollapsibleSection('高级选项', expanded=False)
        self.advanced_panel = AdvancedOptionsPanel(self._get_advanced_value, self._set_advanced_value, show_java=True)
        advanced_group.content_layout.addWidget(self.advanced_panel)
        adv_note = QLabel('版本独立设置中还有更多高级选项可供调整。')
        adv_note.setWordWrap(True)
        adv_note.setStyleSheet('QLabel { background: rgba(0,120,212,0.18); color: #9ccaff; padding: 7px 10px; border-radius: 4px; font-size: 12px; }')
        advanced_group.content_layout.addWidget(adv_note)
        layout.addWidget(advanced_group)
        title_group = self._create_group('标题栏')
        title_layout = title_group.layout()
        self.title_mode_group = QButtonGroup(self)
        self.title_mode_values = ['none', 'default', 'text', 'image']
        self.title_mode_radios = []
        title_mode_row = QHBoxLayout()
        for index, (text, _value) in enumerate(
            (('无', 'none'), ('默认', 'default'), ('文本', 'text'), ('图片', 'image'))
        ):
            radio = QRadioButton(text)
            radio.setStyleSheet(RADIO_BUTTON_STYLE)
            self.title_mode_group.addButton(radio, index)
            radio.toggled.connect(self._on_title_mode_changed)
            title_mode_row.addWidget(radio)
            self.title_mode_radios.append(radio)
        title_mode_row.addStretch(1)
        title_layout.addLayout(title_mode_row)

        self.title_text_widget = QWidget()
        title_text_layout = QVBoxLayout(self.title_text_widget)
        title_text_layout.setContentsMargins(0, 0, 0, 0)
        title_text_layout.setSpacing(8)
        title_row = QHBoxLayout()
        title_text_label = QLabel('标题栏文本')
        title_text_label.setFixedWidth(90)
        title_text_label.setStyleSheet('color: rgba(255,255,255,0.7);')
        title_row.addWidget(title_text_label)
        self.window_title_edit = QLineEdit()
        self.window_title_edit.setPlaceholderText('Spark Craft Launcher')
        self.window_title_edit.setStyleSheet(
            'QLineEdit { background:rgba(255,255,255,0.05);'
            ' border:1px solid rgba(255,255,255,0.1); border-radius:6px;'
            ' color:#f0f0f0; padding:5px 8px; }'
        )
        title_row.addWidget(self.window_title_edit, 1)
        self.window_title_edit.textChanged.connect(self._schedule_title_update)
        delete_title_btn = QPushButton('删除')
        delete_title_btn.setStyleSheet('QPushButton { background:transparent; color:#ff7777; border:1px solid #ff7777; border-radius:6px; padding:6px 12px; } QPushButton:hover { background: rgba(255,100,100,0.12); }')
        delete_title_btn.clicked.connect(self._reset_window_title)
        title_row.addWidget(delete_title_btn)
        title_text_layout.addLayout(title_row)
        color_row = QHBoxLayout()
        color_label = QLabel('标题颜色')
        color_label.setFixedWidth(90)
        color_label.setStyleSheet('color: rgba(255,255,255,0.7);')
        self.window_title_color_edit = QLineEdit()
        self.window_title_color_edit.setPlaceholderText('#88DDDD')
        self.window_title_color_edit.setFixedWidth(120)
        self.window_title_color_edit.setStyleSheet(self.window_title_edit.styleSheet())
        self.window_title_color_edit.editingFinished.connect(self._apply_window_title)
        self.title_color_preview = QLabel('')
        self.title_color_preview.setFixedSize(60, 26)
        self.title_color_preview.setStyleSheet('background:#88DDDD; border-radius:6px; border:1px solid rgba(255,255,255,0.2);')
        color_row.addWidget(color_label)
        color_row.addWidget(self.window_title_color_edit)
        color_row.addWidget(self.title_color_preview)
        color_row.addStretch(1)
        title_text_layout.addLayout(color_row)
        title_layout.addWidget(self.title_text_widget)

        self.title_image_widget = QWidget()
        image_row = QHBoxLayout(self.title_image_widget)
        image_row.setContentsMargins(0, 0, 0, 0)
        image_label = QLabel('标题栏图片')
        image_label.setFixedWidth(90)
        image_label.setStyleSheet('color: rgba(255,255,255,0.7);')
        self.title_image_edit = QLineEdit()
        self.title_image_edit.setPlaceholderText('选择标题栏图片')
        self.title_image_edit.setStyleSheet(self.window_title_edit.styleSheet())
        choose_title_image_btn = QPushButton('选择图片')
        choose_title_image_btn.setStyleSheet(self._secondary_button_style())
        choose_title_image_btn.clicked.connect(self._choose_title_image)
        image_row.addWidget(image_label)
        image_row.addWidget(self.title_image_edit, 1)
        image_row.addWidget(choose_title_image_btn)
        title_layout.addWidget(self.title_image_widget)
        layout.addWidget(title_group)
        bg_group = self._create_group('背景图片')
        bg_layout = bg_group.layout()
        self.bg_count_label = QLabel('背景图片/视频（0 个）')
        self.bg_count_label.setStyleSheet('color: rgba(255,255,255,0.8); font-size: 13px;')
        bg_layout.addWidget(self.bg_count_label)
        fit_row = QHBoxLayout()
        fit_row.addWidget(QLabel('自适应方式'))
        self.bg_fit_combo = QComboBox()
        for key, label in FIT_OPTIONS:
            self.bg_fit_combo.addItem(label, key)
        self.bg_fit_combo.setStyleSheet(self.java_combo.styleSheet())
        self.bg_fit_combo.currentIndexChanged.connect(self._on_background_setting_changed)
        fit_row.addWidget(self.bg_fit_combo, 1)
        bg_layout.addLayout(fit_row)
        loop_row = QHBoxLayout()
        loop_row.addWidget(QLabel('循环模式'))
        self.bg_loop_combo = QComboBox()
        for key, label in LOOP_MODES:
            self.bg_loop_combo.addItem(label, key)
        self.bg_loop_combo.setToolTip('顺序/随机在当前媒体类型内切换；全部顺序/全部随机包含图片和视频')
        self.bg_loop_combo.setStyleSheet(self.java_combo.styleSheet())
        self.bg_loop_combo.currentIndexChanged.connect(self._on_background_loop_mode_changed)
        loop_row.addWidget(self.bg_loop_combo, 1)
        bg_layout.addLayout(loop_row)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel('不透明度'))
        self.bg_opacity_slider = QSlider(Qt.Horizontal)
        self.bg_opacity_slider.setRange(0, 100)
        self.bg_opacity_slider.setValue(80)
        self.bg_opacity_slider.setStyleSheet(self._slider_style())
        self.bg_opacity_slider.valueChanged.connect(self._on_background_setting_changed)
        self.bg_opacity_label = QLabel('80%')
        self.bg_opacity_label.setFixedWidth(45)
        opacity_row.addWidget(self.bg_opacity_slider, 1)
        opacity_row.addWidget(self.bg_opacity_label)
        bg_layout.addLayout(opacity_row)
        blur_row = QHBoxLayout()
        blur_row.addWidget(QLabel('背景模糊'))
        self.bg_blur_slider = QSlider(Qt.Horizontal)
        self.bg_blur_slider.setRange(0, 30)
        self.bg_blur_slider.setValue(0)
        self.bg_blur_slider.setStyleSheet(self._slider_style())
        self.bg_blur_slider.valueChanged.connect(self._on_background_setting_changed)
        self.bg_blur_label = QLabel('0')
        self.bg_blur_label.setFixedWidth(45)
        blur_row.addWidget(self.bg_blur_slider, 1)
        blur_row.addWidget(self.bg_blur_label)
        bg_layout.addLayout(blur_row)
        depth_row = QHBoxLayout()
        depth_row.addWidget(QLabel('GUI 背景深度'))
        self.bg_depth_slider = QSlider(Qt.Horizontal)
        self.bg_depth_slider.setRange(0, 100)
        self.bg_depth_slider.setValue(0)
        self.bg_depth_slider.setToolTip('只加深界面控件，不改变背景图片；背景明暗由不透明度控制')
        self.bg_depth_slider.setStyleSheet(self._slider_style())
        self.bg_depth_slider.valueChanged.connect(self._on_background_setting_changed)
        self.bg_depth_label = QLabel('0')
        self.bg_depth_label.setFixedWidth(45)
        depth_row.addWidget(self.bg_depth_slider, 1)
        depth_row.addWidget(self.bg_depth_label)
        bg_layout.addLayout(depth_row)
        volume_row = QHBoxLayout()
        volume_label = QLabel('背景音量')
        self.bg_volume_slider = QSlider(Qt.Horizontal)
        self.bg_volume_slider.setRange(0, 100)
        self.bg_volume_slider.setValue(70)
        self.bg_volume_slider.setStyleSheet(self._slider_style())
        self.bg_volume_slider.valueChanged.connect(self._on_video_volume_changed)
        self.bg_volume_label = QLabel('70%')
        self.bg_volume_label.setFixedWidth(45)
        volume_row.addWidget(volume_label)
        volume_row.addWidget(self.bg_volume_slider, 1)
        volume_row.addWidget(self.bg_volume_label)
        bg_layout.addLayout(volume_row)
        self.bg_overlay_check = ThemedCheckBox('叠加彩色背景')
        self.bg_overlay_check.toggled.connect(self._on_background_setting_changed)
        bg_layout.addWidget(self.bg_overlay_check)
        self.bg_video_music_check = ThemedCheckBox('播放视频音乐')
        self.bg_video_music_check.setToolTip('勾选后播放视频自带的音乐，并停止自定义背景音乐；播放自定义背景音乐时会自动取消勾选')
        self.bg_video_music_check.toggled.connect(self._on_video_music_toggled)
        bg_layout.addWidget(self.bg_video_music_check)
        self.bg_pause_check = ThemedCheckBox('后台自动暂停')
        self.bg_pause_check.setToolTip('勾选后，启动器最小化或失去焦点时自动暂停背景视频；回到前台后恢复播放')
        self.bg_pause_check.toggled.connect(self._on_video_pause_toggled)
        bg_layout.addWidget(self.bg_pause_check)
        bg_btn_row = QHBoxLayout()
        open_bg_btn = QPushButton('打开文件夹')
        open_bg_btn.clicked.connect(self._open_background_folder)
        open_bg_btn.setStyleSheet(self._secondary_button_style())
        refresh_bg_btn = QPushButton('刷新背景')
        refresh_bg_btn.clicked.connect(self._refresh_background_image)
        refresh_bg_btn.setStyleSheet(self._secondary_button_style())
        clear_bg_btn = QPushButton('清空背景图片')
        clear_bg_btn.clicked.connect(self._clear_background_images)
        clear_bg_btn.setStyleSheet('\n            QPushButton { background: transparent; color:#ff7777;\n                          border:1px solid #ff7777; border-radius:6px; padding:6px 12px; }\n            QPushButton:hover { background: rgba(255,100,100,0.12); }\n        ')
        bg_btn_row.addWidget(open_bg_btn)
        bg_btn_row.addWidget(refresh_bg_btn)
        bg_btn_row.addWidget(clear_bg_btn)
        bg_btn_row.addStretch(1)
        bg_layout.addLayout(bg_btn_row)
        layout.addWidget(bg_group)
        music_group = self._create_group('背景音乐')
        music_layout = music_group.layout()
        self.music_count_label = QLabel('音乐文件（0 首）')
        self.music_count_label.setStyleSheet('color: rgba(255,255,255,0.78); font-size: 13px;')
        music_layout.addWidget(self.music_count_label)
        music_btn_row = QHBoxLayout()
        open_music_btn = QPushButton('打开文件夹')
        open_music_btn.clicked.connect(self._open_music_folder)
        open_music_btn.setStyleSheet(self._secondary_button_style())
        refresh_music_btn = QPushButton('刷新背景音乐')
        refresh_music_btn.clicked.connect(self._refresh_music_files)
        refresh_music_btn.setStyleSheet(self._secondary_button_style())
        clear_music_btn = QPushButton('清空播放')
        clear_music_btn.clicked.connect(self._clear_music_playback)
        clear_music_btn.setStyleSheet('\n            QPushButton { background: transparent; color:#ff7777;\n                          border:1px solid #ff7777; border-radius:6px; padding:6px 12px; }\n            QPushButton:hover { background: rgba(255,100,100,0.12); }\n        ')
        music_btn_row.addWidget(open_music_btn)
        music_btn_row.addWidget(refresh_music_btn)
        music_btn_row.addWidget(clear_music_btn)
        music_btn_row.addStretch(1)
        music_layout.addLayout(music_btn_row)
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel('音乐音量'))
        self.music_volume_slider = QSlider(Qt.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(60)
        self.music_volume_slider.setStyleSheet(self._slider_style())
        self.music_volume_slider.valueChanged.connect(self._on_music_volume_changed)
        self.music_volume_label = QLabel('60%')
        self.music_volume_label.setFixedWidth(45)
        volume_row.addWidget(self.music_volume_slider, 1)
        volume_row.addWidget(self.music_volume_label)
        music_layout.addLayout(volume_row)
        music_hint = QLabel('把音乐文件放入 SCL/music 文件夹，支持 MP3、WAV、OGG、M4A、FLAC。')
        music_hint.setWordWrap(True)
        music_hint.setStyleSheet('color: rgba(255,255,255,0.48); font-size: 12px;')
        music_layout.addWidget(music_hint)
        self.music_autoplay_check = ThemedCheckBox('启动自动播放')
        self.music_autoplay_check.setToolTip('勾选后，启动器启动时自动播放 SCL/music 中的背景音乐')
        self.music_autoplay_check.toggled.connect(self._on_music_autoplay_toggled)
        music_layout.addWidget(self.music_autoplay_check)
        self.music_pause_check = ThemedCheckBox('后台自动暂停')
        self.music_pause_check.setToolTip('勾选后，启动器最小化或失去焦点时自动暂停背景音乐；回到前台后恢复播放')
        self.music_pause_check.toggled.connect(self._on_music_pause_toggled)
        music_layout.addWidget(self.music_pause_check)
        layout.addWidget(music_group)
        community_group = self._create_group('社区资源')
        community_layout = community_group.layout()
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel('来源'))
        self.community_source_combo = QComboBox()
        self.community_source_combo.addItem('尽量使用官方源', 'official')
        self.community_source_combo.addItem('尽量使用镜像源（可能缺少刚刚更新的版本）', 'mirror')
        self.community_source_combo.addItem('仅在官方源加载缓慢时改用镜像源', 'auto')
        self.community_source_combo.setStyleSheet(self.java_combo.styleSheet())
        self.community_source_combo.currentIndexChanged.connect(self._save_community_settings)
        source_row.addWidget(self.community_source_combo, 1)
        community_layout.addLayout(source_row)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel('文件名格式'))
        self.mod_filename_combo = QComboBox()
        self.mod_filename_combo.addItem('[机械动力] create-1.21.1-6.0.4', 'bracket_title')
        self.mod_filename_combo.addItem('【机械动力】 create-1.21.1-6.0.4', 'bracket_title_cn')
        self.mod_filename_combo.addItem('[机械动力]create-1.21.1-6.0.4', 'bracket_title_compact')
        self.mod_filename_combo.addItem('机械动力-create-1.21.1-6.0.4', 'title_filename')
        self.mod_filename_combo.addItem('create-1.21.1-6.0.4-机械动力', 'filename_title')
        self.mod_filename_combo.addItem('create-1.21.1-6.0.4', 'filename')
        self.mod_filename_combo.setStyleSheet(self.java_combo.styleSheet())
        self.mod_filename_combo.currentIndexChanged.connect(self._save_community_settings)
        name_row.addWidget(self.mod_filename_combo, 1)
        community_layout.addLayout(name_row)
        manage_row = QHBoxLayout()
        manage_row.addWidget(QLabel('Mod 管理样式'))
        self.mod_manage_combo = QComboBox()
        self.mod_manage_combo.addItem('标题显示译名，详情显示文件名', 'title_translated')
        self.mod_manage_combo.addItem('标题显示文件名，详情显示译名', 'title_filename')
        self.mod_manage_combo.setStyleSheet(self.java_combo.styleSheet())
        self.mod_manage_combo.currentIndexChanged.connect(self._save_community_settings)
        manage_row.addWidget(self.mod_manage_combo, 1)
        community_layout.addLayout(manage_row)
        layout.addWidget(community_group)
        mirror_group = self._create_group('下载镜像')
        mirror_layout = mirror_group.layout()
        source_options = [('优先使用官方源，在加载缓慢时换用镜像源', 'auto'), ('优先使用 BMCLAPI 镜像源', 'bmclapi'), ('仅使用官方源', 'official')]
        file_row = QHBoxLayout()
        file_label = QLabel('文件下载源')
        file_label.setFixedWidth(90)
        file_label.setStyleSheet('color: rgba(255,255,255,0.7);')
        self.file_source_combo = QComboBox()
        for text, value in source_options:
            self.file_source_combo.addItem(text, value)
        self.file_source_combo.setMinimumWidth(300)
        self.file_source_combo.setStyleSheet(self.java_combo.styleSheet())
        self.file_source_combo.currentIndexChanged.connect(self._auto_save)
        file_row.addWidget(file_label)
        file_row.addWidget(self.file_source_combo, 1)
        mirror_layout.addLayout(file_row)
        version_row = QHBoxLayout()
        version_label = QLabel('版本列表源')
        version_label.setFixedWidth(90)
        version_label.setStyleSheet('color: rgba(255,255,255,0.7);')
        self.version_source_combo = QComboBox()
        for text, value in source_options:
            self.version_source_combo.addItem(text, value)
        self.version_source_combo.setMinimumWidth(300)
        self.version_source_combo.setStyleSheet(self.java_combo.styleSheet())
        self.version_source_combo.currentIndexChanged.connect(self._auto_save)
        version_row.addWidget(version_label)
        version_row.addWidget(self.version_source_combo, 1)
        mirror_layout.addLayout(version_row)
        layout.addWidget(mirror_group)
        mod_source_group = self._create_group('模组下载源')
        mod_source_layout = mod_source_group.layout()
        source_tip = QLabel('下载中心默认使用 Modrinth，无需配置即可使用。CurseForge 需要官方 API Key（免费申请）。')
        source_tip.setWordWrap(True)
        source_tip.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 12px;')
        mod_source_layout.addWidget(source_tip)
        cf_row = QHBoxLayout()
        cf_label = QLabel('CurseForge API Key')
        cf_label.setFixedWidth(140)
        cf_label.setStyleSheet('color: rgba(255,255,255,0.7);')
        self.cf_key_edit = QLineEdit()
        self.cf_key_edit.setEchoMode(QLineEdit.Password)
        self.cf_key_edit.setPlaceholderText('粘贴 CurseForge API Key（留空则不可用）')
        self.cf_key_edit.setStyleSheet('\n            QLineEdit {\n                background: rgba(255,255,255,0.05);\n                border: 1px solid rgba(255,255,255,0.1);\n                border-radius: 6px; color: #f0f0f0; padding: 4px 8px;\n            }\n        ')
        self.cf_key_edit.editingFinished.connect(self._save_cf_key)
        cf_row.addWidget(cf_label)
        cf_row.addWidget(self.cf_key_edit, 1)
        get_key_btn = QPushButton('申请 Key')
        get_key_btn.setStyleSheet('QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:6px 12px; } QPushButton:hover { background:#4a4a4a; }')
        get_key_btn.clicked.connect((lambda: webbrowser.open('https://console.curseforge.com/')))
        cf_row.addWidget(get_key_btn)
        mod_source_layout.addLayout(cf_row)
        self.cf_key_status = QLabel('')
        self.cf_key_status.setWordWrap(True)
        self.cf_key_status.setStyleSheet('color: rgba(255,255,255,0.45); font-size: 12px;')
        mod_source_layout.addWidget(self.cf_key_status)
        layout.addWidget(mod_source_group)
        lang_group = self._create_group('语言设置')
        lang_layout = QHBoxLayout()
        self.chinese_checkbox = ThemedCheckBox('启动时自动设置游戏语言为简体中文')
        self.chinese_checkbox.stateChanged.connect(self._save_chinese_opt)
        lang_layout.addWidget(self.chinese_checkbox)
        lang_layout.addStretch(1)
        lang_group.layout().addLayout(lang_layout)
        layout.addWidget(lang_group)
        log_group = self._create_group('日志')
        log_layout = QVBoxLayout()
        log_desc = QLabel(f'所有启动器、安装器和游戏输出日志统一保存在：\n{LOG_DIR}')
        log_desc.setWordWrap(True)
        log_desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        log_desc.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 12px;')
        log_layout.addWidget(log_desc)
        log_btn_row = QHBoxLayout()
        open_log_btn = QPushButton('打开日志文件夹')
        open_log_btn.clicked.connect((lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR)))))
        open_log_btn.setStyleSheet(self._secondary_button_style())
        clear_log_btn = QPushButton('清空日志')
        clear_log_btn.clicked.connect(self._on_clear_logs)
        clear_log_btn.setStyleSheet('\n            QPushButton { background:#6c2a2a; color:white; border:none;\n                          border-radius:6px; padding:6px 16px; }\n            QPushButton:hover { background:#8a3a3a; }\n        ')
        log_btn_row.addWidget(open_log_btn)
        log_btn_row.addWidget(clear_log_btn)
        log_btn_row.addStretch(1)
        log_layout.addLayout(log_btn_row)
        log_group.layout().addLayout(log_layout)
        layout.addWidget(log_group)
        reset_group = self._create_group('恢复默认设置')
        reset_layout = QVBoxLayout()
        reset_hint = QLabel('将启动器设置恢复为默认值；账号、登录凭据、游戏目录和本地背景文件会保留。')
        reset_hint.setWordWrap(True)
        reset_hint.setStyleSheet('color: rgba(255,255,255,0.6); font-size: 12px;')
        reset_layout.addWidget(reset_hint)
        reset_row = QHBoxLayout()
        reset_btn = QPushButton('恢复默认设置')
        reset_btn.clicked.connect(self._on_reset_settings)
        reset_btn.setStyleSheet('\n            QPushButton { background:transparent; color:#ff8888;\n                          border:1px solid #ff7777; border-radius:6px; padding:7px 16px; }\n            QPushButton:hover { background:rgba(255,100,100,0.12); }\n        ')
        reset_row.addWidget(reset_btn)
        reset_row.addStretch(1)
        reset_layout.addLayout(reset_row)
        reset_group.layout().addLayout(reset_layout)
        layout.addWidget(reset_group)

        self.offline_skin = OfflineSkinSettings(self)
        self.offline_skin_section = CollapsibleSection('离线皮肤', expanded=False)
        self.offline_skin_section.content_layout.addWidget(self.offline_skin)

        self.version_settings_btn = QPushButton('→  版本独立设置')
        self.version_settings_btn.setMinimumHeight(40)
        self.version_settings_btn.setCursor(Qt.PointingHandCursor)
        self.version_settings_btn.setStyleSheet(
            'QPushButton { background:#0078d4; color:white; border:none;'
            ' border-radius:8px; padding:8px 32px; font-size:13px; font-weight:900; }'
            'QPushButton:hover { background:#1084e0; }'
            'QPushButton:pressed { background:#006cbd; }'
        )
        self.version_settings_btn.clicked.connect(self._open_selected_version_settings)

        def make_page():
            page_scroll = QScrollArea()
            page_scroll.setWidgetResizable(True)
            page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            page_scroll.setStyleSheet('QScrollArea { background:transparent; border:none; }')
            page_content = QWidget()
            page_layout = QVBoxLayout(page_content)
            page_layout.setContentsMargins(0, 16, 0, 30)
            page_layout.setSpacing(16)
            page_scroll.setWidget(page_content)
            return page_scroll, page_layout

        self.startup_scroll, startup_layout = make_page()
        self.personal_scroll, personal_layout = make_page()
        self.other_scroll, other_layout = make_page()
        self.settings_tabs = QTabWidget()
        self.settings_tabs.setDocumentMode(False)
        self.settings_tabs.setStyleSheet(
            'QTabWidget { border:none; background:transparent; }'
            'QTabWidget::pane { border:none; background:transparent; }'
            'QTabWidget::tab-bar { border:none; background:transparent; left:0px; }'
            'QTabBar { border:none; background:transparent; }'
            'QTabBar::tab { background:rgba(255,255,255,0.16); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.24); padding:8px 22px; margin-right:6px; margin-bottom:8px;'
            ' border-radius:8px; font-size:11pt; }'
            'QTabBar::tab:selected { background:#0078d4; color:white; border-color:#0078d4; }'
            'QTabBar::tab:hover { background:rgba(255,255,255,0.24); }'
        )
        self.settings_tabs.addTab(self.startup_scroll, '启动')
        self.settings_tabs.setTabIcon(0, painted_icon('play', 16, '#aab4c0'))
        self.settings_tabs.addTab(self.personal_scroll, '个性化')
        self.settings_tabs.setTabIcon(1, painted_icon('shirt', 16, '#aab4c0'))
        self.settings_tabs.addTab(self.other_scroll, '其他')
        self.settings_tabs.setTabIcon(2, painted_icon('grid', 16, '#aab4c0'))

        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not title:
                widget.setParent(None)
        title.setFont(ui_font(24, QFont.DemiBold))
        title.setStyleSheet('color:#f0f0f0;')
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(40, 24, 40, 8)
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        for group in (launch_group, mem_group, self.offline_skin_section, advanced_group):
            startup_layout.addWidget(group)
        startup_layout.addStretch(1)
        startup_layout.addWidget(self.version_settings_btn, 0, Qt.AlignHCenter)
        for group in (theme_group, bg_group, music_group, title_group):
            personal_layout.addWidget(group)
        personal_layout.addStretch(1)
        for group in (java_group, thread_group, community_group, mirror_group, mod_source_group, lang_group, log_group, reset_group):
            other_layout.addWidget(group)
        other_layout.addStretch(1)
        main_layout.addWidget(header)
        tab_host = QWidget()
        tab_host_layout = QVBoxLayout(tab_host)
        tab_host_layout.setContentsMargins(40, 0, 40, 0)
        tab_host_layout.setSpacing(0)
        tab_host_layout.addWidget(self.settings_tabs)
        main_layout.addWidget(tab_host, 1)
        scroll.deleteLater()
        content.deleteLater()
        self.settings_tabs.currentChanged.connect(self._animate_settings_tab_switch)
        self._update_version_settings_button()

    def _selected_installed_version(self):
        version_id = str(self.config.get('selected_version', '') or '').strip()
        if not version_id:
            return ''
        try:
            version_dir = (
                Path(self.config.get_mc_dir()) / 'versions' / version_id
            )
            return version_id if version_dir.is_dir() else ''
        except Exception:
            return ''

    def _update_version_settings_button(self):
        if not hasattr(self, 'version_settings_btn'):
            return
        version_id = self._selected_installed_version()
        self.version_settings_btn.setVisible(bool(version_id))
        if version_id:
            self.version_settings_btn.setToolTip(
                f'打开 {version_id} 的版本独立设置'
            )

    def _open_selected_version_settings(self):
        version_id = self._selected_installed_version()
        if not version_id:
            self._update_version_settings_button()
            return
        window = self.window()
        opener = getattr(window, 'open_game_settings', None)
        if callable(opener):
            opener(version_id)

    def open_offline_skin(self):
        try:
            self.settings_tabs.setCurrentIndex(0)
            self.offline_skin_section.set_expanded(True)
            self.offline_skin.load_settings()
            QTimer.singleShot(
                80,
                lambda: self.startup_scroll.ensureWidgetVisible(
                    self.offline_skin_section, 20, 20
                ),
            )
        except Exception:
            pass

    def _animate_settings_tab_switch(self, index):
        widget = self.settings_tabs.widget(index)
        if widget is None:
            return
        self._stop_settings_tab_animation()
        QTimer.singleShot(0, lambda w=widget: self._fade_settings_tab(w))

    def _stop_settings_tab_animation(self):
        animation = self._settings_tab_animation
        effect = self._settings_tab_effect
        widget = self._settings_tab_effect_widget
        self._settings_tab_animation = None
        self._settings_tab_effect = None
        self._settings_tab_effect_widget = None
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

    def _fade_settings_tab(self, widget):
        if self._settings_tab_animation is not None:
            return
        try:
            if widget is not self.settings_tabs.currentWidget() or not widget.isVisible():
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
            if self._settings_tab_effect is not effect:
                return
            self._settings_tab_animation = None
            self._settings_tab_effect = None
            self._settings_tab_effect_widget = None
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass

        animation.finished.connect(finish)
        self._settings_tab_animation = animation
        self._settings_tab_effect = effect
        self._settings_tab_effect_widget = widget
        animation.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_version_settings_button()

    def shutdown(self):
        self._stop_settings_tab_animation()
        try:
            self.offline_skin.shutdown()
        except Exception:
            pass

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _create_group(self, title):
        group = QGroupBox()
        group.setStyleSheet('\n            QGroupBox {\n                background: rgba(18,20,26,0.58);\n                border: 1px solid rgba(255,255,255,0.22);\n                border-radius: 12px;\n                margin-top: 0px;\n                padding-top: 8px;\n            }\n        ')
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(24, 18, 24, 18)
        group_layout.setSpacing(12)
        title_label = QLabel(title)
        title_label.setStyleSheet('color: rgba(255,255,255,0.85); font-size: 15px; font-weight:900;')
        group_layout.addWidget(title_label)
        return group

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
        self._update_memory_usage()

    def _on_memory_optimize_toggled(self, checked):
        self.config.set('memory_optimize_before_launch', bool(checked))
        self.config.save()

    def _update_memory_usage(self):
        if not hasattr(self, 'memory_usage_label'):
            return
        try:
            import psutil
            mem = psutil.virtual_memory()
            total_mb = mem.total / 1048576
            used_mb = mem.used / 1048576
            percent = int(mem.percent) if mem.percent is not None else 0
            self.memory_usage_label.setText(
                f'已使用内存 {self._format_mem(used_mb)} / {self._format_mem(total_mb)}'
            )
        except Exception:
            used_mb = 0.0
            total_mb = 0.0
            self.memory_usage_label.setText('已使用内存 -- / --')
        total_mb = max(1.0, total_mb)
        used_mb = max(0.0, min(total_mb, used_mb))
        alloc_mb = max(0.0, float(self.mem_slider.value()))
        self.memory_usage_bar.set_values(used_mb / total_mb, alloc_mb / total_mb)
        self.memory_alloc_label.setText(
            f'游戏分配 {self._format_mem(alloc_mb)}'
        )

    def _auto_calculate_mem(self):
        """按 PCL 的动态内存算法计算：可用内存 + 版本 Mod 数量分阶段分配。"""

        try:
            from core.memory import count_mods, recommended_ram_mb
            mc_dir = self.config.get_mc_dir()
            version_id = str(self.config.get('selected_version') or '')
            loader = ''
            try:
                from ui.version_meta import detect_installed_loader
                loader = detect_installed_loader(mc_dir, version_id) if version_id else ''
            except Exception:
                loader = ''
            mod_count = count_mods(mc_dir, version_id)
            recommended = recommended_ram_mb(mc_dir, version_id, loader, mod_count)
            return int(recommended), mod_count
        except Exception:
            return 2048, 0

    def _update_live_memory(self):
        if not hasattr(self, 'mem_slider'):
            return
        try:
            if not self.auto_radio.isChecked():
                return
            recommended, mod_count = self._auto_calculate_mem()
            current = self.mem_slider.value()
            if abs(recommended - current) >= 256:
                self.mem_slider.setValue(recommended)
                self.config.set('memory_max', self._format_mem(recommended), auto_save=False)
                self.config.save()
                self.settings_changed.emit()
            self.auto_info_label.setText(
                f'PCL 动态分配：检测到 {mod_count} 个 Mod，按当前可用内存建议 {self._format_mem(recommended)}'
            )
        except RuntimeError:
            pass
        except Exception:
            pass

    def _on_mem_mode_toggled(self):
        if self.custom_radio.isChecked():
            self.mem_slider.setEnabled(True)
            self.auto_info_label.setText('')
            self.config.set('mem_mode', 'custom', auto_save=False)
            self.config.set('memory_max', self._format_mem(self.mem_slider.value()), auto_save=False)
        else:
            self.mem_slider.setEnabled(False)
            recommended, mod_count = self._auto_calculate_mem()
            self.mem_slider.setValue(recommended)
            self._update_mem_label()
            self.auto_info_label.setText(f'PCL 动态分配：检测到 {mod_count} 个 Mod，建议 {self._format_mem(recommended)}')
            self.config.set('mem_mode', 'auto', auto_save=False)
            self.config.set('memory_max', self._format_mem(recommended), auto_save=False)
        self.config.save()
        self.settings_changed.emit()

    def _on_mem_slider_changed(self, value):
        self._update_mem_label()
        if self.custom_radio.isChecked():
            self.config.set('memory_max', self._format_mem(value), auto_save=False)
            self.config.save()
            self.settings_changed.emit()

    def _on_threads_changed(self, value):
        self.thread_value_label.setText(str(value))
        self.config.set('download_threads', value, auto_save=False)
        self._download_save_timer.start()
        from core.net import DownloadGate
        DownloadGate.configure(value)
        if value > 10:
            if not getattr(self, '_threads_warned', False):
                self._threads_warned = True
                from ui.widgets.toast import show_toast
                show_toast('过多的下载线程同时下载会导致严重的设备网络卡顿！', 'warning', self)
                return
            return
        self._threads_warned = False

    def _on_game_window_size_changed(self):
        is_custom = self.game_window_size_combo.currentData() == 'custom'
        self.custom_size_widget.setVisible(is_custom)
        self._save_launch_options()

    def _on_default_iso_changed(self, index=0):
        value = str(self.default_iso_combo.currentData() or 'off')
        self._save_launch_options()
        if value != 'off':
            if str(getattr(self, '_last_iso_value', 'off') or 'off') == 'off':
                dialog = DarkQuestionDialog(self, '提示', '本设置仅会对之后新安装的版本生效。\n如果要修改已安装的版本的隔离方式，请在它的版本独立设置中调整。', actions=[('确定', 'ok', True)])
                dialog.exec()
        self._last_iso_value = value

    def _save_launch_options(self):
        if not hasattr(self, 'default_iso_combo'):
            return
        self.config.set('default_isolation', self.default_iso_combo.currentData(), auto_save=False)
        self.config.set('game_window_title', self.game_title_edit.text().strip(), auto_save=False)
        self.config.set('launch_custom_info', self.custom_info_edit.text().strip(), auto_save=False)
        self.config.set('launcher_visibility', self.launcher_visibility_combo.currentData(), auto_save=False)
        self.config.set('game_process_priority', self.process_priority_combo.currentData(), auto_save=False)
        self.config.set('game_window_size', self.game_window_size_combo.currentData(), auto_save=False)
        self.config.set('game_window_width', self.game_width_spin.value(), auto_save=False)
        self.config.set('game_window_height', self.game_height_spin.value(), auto_save=False)
        self.config.save()

    def _on_speed_limit_changed(self, value):
        mb = 0.0 if value >= 1000 else max(0.1, value / 10.0)
        self.speed_limit_label.setText('无限制' if mb <= 0 else f'{mb:.1f} MB/s')
        self.config.set('download_speed_limit', mb, auto_save=False)
        self._download_save_timer.start()
        from core.net import DownloadSpeedLimiter
        DownloadSpeedLimiter.configure(mb)

    def _refresh_dir_list(self):
        if getattr(self, 'dir_list', None) is None:
            return
        self.dir_list.blockSignals(True)
        self.dir_list.clear()
        dirs = self.config.get('game_dirs', [])
        valid = [d for d in dirs if Path(d).exists()]
        if not valid:
            if getattr(sys, 'frozen', False):
                launcher_mc = Path(sys.executable).resolve().parent / '.minecraft'
            else:
                launcher_mc = Path(__file__).resolve().parent / '.minecraft'
            if launcher_mc.exists():
                valid = [str(launcher_mc)]
            else:
                launcher_mc.mkdir(parents=True, exist_ok=True)
                valid = [str(launcher_mc)]
            self.config.set('game_dirs', valid, auto_save=False)
        selected = self.config.get('selected_game_dir', '')
        for d in valid:
            item = QListWidgetItem(d)
            if d == selected:
                item.setForeground(Qt.green)
                item.setText(f'✓ {d}')
            self.dir_list.addItem(item)
        if selected and Path(selected).exists():
            self.dir_status.setText(f'当前游戏目录：{selected}')
        else:
            if self.dir_list.count() > 0:
                first = self.dir_list.item(0).text()
                if first.startswith('✓ '):
                    first = first[2:]
                self.config.set('selected_game_dir', first, auto_save=False)
                self.config.set('mc_dir', first, auto_save=False)
                self.config.save()
                self.dir_list.blockSignals(False)
                self.settings_changed.emit()
                return
            self.dir_status.setText('未选择有效游戏目录')
        self.dir_list.blockSignals(False)

    def _on_dir_selection_changed(self):
        if getattr(self, 'dir_list', None) is None:
            return
        item = self.dir_list.currentItem()
        if item is None:
            return
        path = item.text()
        if path.startswith('✓ '):
            path = path[2:]
        if not path or not Path(path).exists():
            return
        current = self.config.get('selected_game_dir', '')
        if path == current:
            return
        self.config.set('selected_game_dir', path, auto_save=False)
        self.config.set('mc_dir', path, auto_save=False)
        self.config.save()
        self._refresh_dir_list()
        self.settings_changed.emit()

    def _on_add_directory(self):
        if getattr(self, 'dir_list', None) is None:
            return
        path = QFileDialog.getExistingDirectory(self, '选择 .minecraft 目录')
        if not path:
            return
        p = Path(path)
        if not ((p / 'versions').exists() or (p / 'launcher_profiles.json').exists()):
            reply = QMessageBox.question(self, '确认', '该目录似乎不是有效的 .minecraft 文件夹，是否继续添加？', QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        dirs = self.config.get('game_dirs', [])
        if path in dirs:
            QMessageBox.warning(self, '重复', '该目录已在列表中')
            return
        dirs.append(path)
        self.config.set('game_dirs', dirs, auto_save=False)
        self.config.save()
        self._refresh_dir_list()
        self.settings_changed.emit()

    def _on_remove_directory(self):
        if getattr(self, 'dir_list', None) is None:
            return
        item = self.dir_list.currentItem()
        if not item:
            return
        path = item.text()
        if path.startswith('✓ '):
            path = path[2:]
        current = self.config.get('selected_game_dir', '')
        if path == current:
            QMessageBox.warning(self, '无法移除', '不能移除当前正在使用的游戏目录')
            return
        dirs = self.config.get('game_dirs', [])
        if path in dirs:
            dirs.remove(path)
            self.config.set('game_dirs', dirs, auto_save=False)
            self.config.save()
            self._refresh_dir_list()
            self.settings_changed.emit()

    def _get_java_version(self, java_path):
        '''复用 LaunchManager 的版本解析'''

        return self.launch_mgr._get_java_version(java_path)

    def _refresh_java_combo(self, java_list):
        self.java_combo.blockSignals(True)
        try:
            current_path = str(self.config.get('java_path', '') or '')
            self.java_combo.clear()
            self._cached_java_list = list(java_list or [])
            self.java_combo.addItem('自动选择最佳版本', '')
            entries = list(java_list or [])
            # 手工导入的 Java 永久保留，不因重新检测或扫描结果为空而消失
            if current_path and Path(current_path).exists():
                if not any(str(p) == current_path for _v, p in entries):
                    ver = self._get_java_version(current_path) or '自定义'
                    entries.append((ver, current_path))
            if not entries:
                self.java_combo.addItem('未检测到Java', None)
                self.java_combo.setCurrentIndex(0)
                self.java_status.setText('未检测到 Java 环境，请手动安装或点击「导入」选择')
                self.java_status.setStyleSheet('color: #fbbf24; font-size: 12px;')
                return
            found_index = -1
            for i, (ver, path) in enumerate(entries):
                display = f'Java {ver}  [路径: {path}]'
                self.java_combo.addItem(display, path)
                if str(path) == current_path and Path(path).exists():
                    found_index = i + 1
            if found_index >= 0:
                self.java_combo.setCurrentIndex(found_index)
                self.java_status.setText(f'✅ 当前使用: Java {entries[found_index - 1][0]}')
                self.java_status.setStyleSheet('color: #4ade80; font-size: 12px;')
            elif current_path and Path(current_path).exists():
                self.java_combo.addItem(f'自定义: {current_path}', current_path)
                self.java_combo.setCurrentIndex(self.java_combo.count() - 1)
                self.java_status.setText('⚠️ 当前使用自定义 Java')
                self.java_status.setStyleSheet('color: #fbbf24; font-size: 12px;')
            else:
                self.java_combo.setCurrentIndex(0)
                self.java_status.setText('✅ 将自动选择最佳 Java 版本')
                self.java_status.setStyleSheet('color: #4ade80; font-size: 12px;')
                if current_path:
                    self.config.set('java_path', '', auto_save=False)
                    self.config.save()
        finally:
            self.java_combo.blockSignals(False)

    def _on_detect_java(self):
        if self.java_scan.is_running():
            return
        self._manual_java_detect = True
        self.java_status.setText('正在检测 Java 环境...')
        self.java_status.setStyleSheet('color: rgba(255,255,255,0.5); font-size: 12px;')
        self.java_scan.start(force=True)

    def _on_java_scan_finished(self, java_list):
        self._refresh_java_combo(java_list)
        if self._manual_java_detect:
            self._manual_java_detect = False
            self.java_status.setText(f'✅ 更新完成，当前有 {len(java_list)} 个 Java')
            self.java_status.setStyleSheet('color: #4ade80; font-size: 12px;')
            self.settings_changed.emit()

    def _on_java_selected(self, index):
        if index < 0:
            return
        if index == 0:
            self.config.set('java_path', '', auto_save=False)
            self.config.save()
            self.java_status.setText('✅ 将自动选择最佳 Java 版本')
            self.java_status.setStyleSheet('color: #4ade80; font-size: 12px;')
            self._sync_advanced_java()
            self.settings_changed.emit()
            return
        path = self.java_combo.itemData(index)
        if path and Path(path).exists():
            self.config.set('java_path', path, auto_save=False)
            self.config.save()
            ver = '未知版本'
            for v, p in self._cached_java_list:
                if p == path:
                    ver = v
                    break
            self.java_status.setText(f'✅ 已选择: Java {ver}')
            self.java_status.setStyleSheet('color: #4ade80; font-size: 12px;')
            self._sync_advanced_java()
            self.settings_changed.emit()
            return
        self.java_status.setText('❌ 该 Java 路径不存在或已移动，请点击「检测Java」刷新')
        self.java_status.setStyleSheet('color: #f87171; font-size: 12px;')

    def _sync_advanced_java(self):
        try:
            if hasattr(self, 'advanced_panel'):
                self.advanced_panel._reload_java()
            return
        except Exception:
            pass

    def _on_import_java(self):
        file_filter = 'Java 可执行文件 (javaw.exe java.exe);;所有文件 (*.*)'
        path, _ = QFileDialog.getOpenFileName(self, '选择 Java 可执行文件', '', file_filter)
        if not path:
            return
        for i in range(self.java_combo.count()):
            if self.java_combo.itemData(i) == path:
                self.java_combo.setCurrentIndex(i)
                break
        else:
            ver = self._get_java_version(path) or '自定义'
            self.java_combo.addItem(f'Java {ver}  [路径: {path}]', path)
            self.java_combo.setCurrentIndex(self.java_combo.count() - 1)
            self.config.set('java_path', path, auto_save=False)
            self.config.save()
            self.java_status.setText(f'✅ 已导入 Java: {ver}')
            self.java_status.setStyleSheet('color: #4ade80; font-size: 12px;')
            self._sync_advanced_java()
            self.settings_changed.emit()

    def _load_settings(self):
        self._refresh_dir_list()
        theme = str(self.config.get('theme', 'dark') or 'dark')
        if theme not in self.theme_buttons:
            theme = 'system'
        for key, button in self.theme_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == theme)
            button.blockSignals(False)
        java_list = self.launch_mgr.peek_cached_java_list()
        if java_list is None:
            self.java_combo.blockSignals(True)
            self.java_combo.clear()
            self.java_combo.addItem('自动选择最佳版本', '')
            self.java_combo.blockSignals(False)
        else:
            self._refresh_java_combo(java_list)
        mem_mode = self.config.get('mem_mode', 'auto')
        mem_max = self.config.get('memory_max', '4G')
        self.auto_radio.blockSignals(True)
        self.custom_radio.blockSignals(True)
        if mem_mode == 'auto':
            self.auto_radio.setChecked(True)
        else:
            self.custom_radio.setChecked(True)
        self.auto_radio.blockSignals(False)
        self.custom_radio.blockSignals(False)
        size = self._parse_mem(mem_max)
        self.mem_slider.setValue(size)
        optimize = bool(self.config.get('memory_optimize_before_launch', False))
        self.memory_optimize_check.blockSignals(True)
        self.memory_optimize_check.setChecked(optimize)
        self.memory_optimize_check.blockSignals(False)
        self._update_mem_label()
        threads = int(self.config.get('download_threads', 64) or 64)
        threads = max(1, min(256, threads))
        self.thread_slider.blockSignals(True)
        self.thread_slider.setValue(threads)
        self.thread_value_label.setText(str(threads))
        self.thread_slider.blockSignals(False)
        speed = float(self.config.get('download_speed_limit', 0) or 0)
        speed = max(0.0, min(100.0, speed))
        self.speed_limit_slider.blockSignals(True)
        self.speed_limit_slider.setValue(1000 if speed <= 0 else min(999, int(round(speed * 10))))
        self.speed_limit_slider.blockSignals(False)
        self.speed_limit_label.setText('无限制' if speed <= 0 else f'{speed:.1f} MB/s')
        try:
            from core.net import DownloadGate, DownloadSpeedLimiter
            DownloadGate.configure(threads)
            DownloadSpeedLimiter.configure(speed)
        except Exception:
            pass
        for combo, key in (self.file_source_combo, 'file_download_source'), (self.version_source_combo, 'version_list_source'):
            combo.blockSignals(True)
            idx = combo.findData(self.config.get(key, 'auto') or 'auto')
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        for combo, key, default in (self.default_iso_combo, 'default_isolation', 'off'), (self.launcher_visibility_combo, 'launcher_visibility', 'keep'), (self.process_priority_combo, 'game_process_priority', 'normal'), (self.game_window_size_combo, 'game_window_size', 'default'):
            combo.blockSignals(True)
            idx = combo.findData(self.config.get(key, default) or default)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        self._last_iso_value = str(self.default_iso_combo.currentData() or 'off')
        self.game_title_edit.blockSignals(True)
        self.game_title_edit.setText(self.config.get('game_window_title', '') or '')
        self.game_title_edit.blockSignals(False)
        self.custom_info_edit.blockSignals(True)
        self.custom_info_edit.setText(self.config.get('launch_custom_info', '') or '')
        self.custom_info_edit.blockSignals(False)
        self.game_width_spin.blockSignals(True)
        self.game_width_spin.setValue(int(self.config.get('game_window_width', 854) or 854))
        self.game_width_spin.blockSignals(False)
        self.game_height_spin.blockSignals(True)
        self.game_height_spin.setValue(int(self.config.get('game_window_height', 480) or 480))
        self.game_height_spin.blockSignals(False)
        self.custom_size_widget.setVisible(self.game_window_size_combo.currentData() == 'custom')
        self.cf_key_edit.blockSignals(True)
        self.cf_key_edit.setText(self.config.get('curseforge_api_key', '') or '')
        self.cf_key_edit.blockSignals(False)
        self._update_cf_key_status()
        self.chinese_checkbox.blockSignals(True)
        auto_chinese = self.config.get('auto_set_chinese', True)
        self.chinese_checkbox.setChecked(auto_chinese)
        self.chinese_checkbox.blockSignals(False)
        self._on_mem_mode_toggled()
        self._load_background_ui()
        self._load_music_ui()
        self._load_community_ui()
        self._update_version_settings_button()

    def _save_cf_key(self):
        key = self.cf_key_edit.text().strip()
        self.config.set('curseforge_api_key', key)
        self._update_cf_key_status()
        self.settings_changed.emit()

    def _update_cf_key_status(self):
        if self.cf_key_edit.text().strip():
            self.cf_key_status.setText('CurseForge 源已启用；Modrinth 仍为默认下载源。')
            self.cf_key_status.setStyleSheet('color: #4ade80; font-size: 12px;')
            return
        self.cf_key_status.setText('未填写 API Key：CurseForge 源不可用，下载中心将使用 Modrinth。')
        self.cf_key_status.setStyleSheet('color: rgba(255,255,255,0.45); font-size: 12px;')

    def _save_chinese_opt(self):
        val = self.chinese_checkbox.isChecked()
        self.config.set('auto_set_chinese', val)
        self.settings_changed.emit()

    def _get_advanced_value(self, key, default=None):
        value = self.config.get(key, default)
        if value is None:
            return default
        return value

    def _set_advanced_value(self, key, value):
        self.config.set(key, value)
        if key == 'java_path':
            idx = self.java_combo.findData(value)
            if idx < 0 and value:
                self.java_combo.addItem(f'自定义: {value}', value)
                idx = self.java_combo.count() - 1
            self.java_combo.blockSignals(True)
            self.java_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.java_combo.blockSignals(False)

    def _ok_button_style(self):
        return 'QPushButton { background:#0078d4; color:white; border:none; border-radius:6px; padding:6px 14px; } QPushButton:hover { background:#106ebe; }'

    def _on_reset_settings(self):
        reply = QMessageBox.question(self, '恢复默认设置', '确定将所有设置恢复为默认值吗？\n账号、登录凭据、游戏目录和本地背景文件会保留。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.config.reset_to_defaults()
            self._load_settings()
            try:
                self.advanced_panel.reload()
            except Exception:
                pass
            self.title_changed.emit(self.config.get('global_window_title', ''))
            self.background_changed.emit()
            self.settings_changed.emit()
            QMessageBox.information(self, '恢复完成', '所有设置已恢复默认值。')
        except Exception as exc:
            QMessageBox.critical(self, '恢复失败', str(exc))
            return

    def _on_clear_logs(self):
        reply = QMessageBox.question(self, '清空日志', f'确定清空 {LOG_DIR} 下的所有日志文件吗？\n正在运行的游戏日志如果被占用会跳过。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        removed, errors = clear_logs()
        if errors:
            QMessageBox.warning(self, '部分日志未清空', f'已删除 {removed} 个文件，以下文件无法删除：\n' + '\n'.join(errors[:8]))
            return
        QMessageBox.information(self, '清空完成', f'已删除 {removed} 个日志文件。')

    def _secondary_button_style(self):
        return 'QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:6px 14px; } QPushButton:hover { background:#4a4a4a; }'

    def _slider_style(self):
        return '\n            QSlider::groove:horizontal { background:#444; height:8px; border-radius:4px; }\n            QSlider::handle:horizontal { background:#0078d4; width:18px;\n                                         margin:-5px 0; border-radius:9px; }\n        '

    def _schedule_title_update(self):
        self._title_timer.start()

    def _apply_window_title(self):
        title = self.window_title_edit.text().strip()
        color = self.window_title_color_edit.text().strip() or '#88DDDD'
        if not (re.fullmatch('#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?', color) and QColor(color).isValid()):
            if self.window_title_color_edit.hasFocus():
                QMessageBox.warning(self, '颜色格式错误', '标题颜色请填写 #RRGGBB 或 #AARRGGBB 格式。')
            color = self.config.get('global_title_color', '#88DDDD') or '#88DDDD'
            self.window_title_color_edit.setText(color)
        self.config.set('global_window_title', title)
        self.config.set('global_title_color', color)
        self.title_color_preview.setStyleSheet(f'background:{color}; border-radius:6px; border:1px solid rgba(255,255,255,0.2);')
        self.title_changed.emit(title)

    def _reset_window_title(self):
        self.window_title_edit.setText('')
        self.window_title_color_edit.setText('#88DDDD')
        self.config.set('global_window_title', '')
        self.config.set('global_title_color', '#88DDDD')
        self.title_color_preview.setStyleSheet('background:#88DDDD; border-radius:6px; border:1px solid rgba(255,255,255,0.2);')
        self.title_changed.emit('')

    def _current_title_mode(self):
        index = self.title_mode_group.checkedId()
        if 0 <= index < len(self.title_mode_values):
            return self.title_mode_values[index]
        return 'text'

    def _set_title_mode_radio(self, mode):
        try:
            index = self.title_mode_values.index(str(mode or 'text'))
        except ValueError:
            index = 2
        radio = self.title_mode_group.button(index)
        if radio is not None:
            radio.setChecked(True)

    def _apply_title_mode_visibility(self):
        mode = self._current_title_mode()
        self.title_text_widget.setVisible(mode == 'text')
        self.title_image_widget.setVisible(mode == 'image')

    def _on_title_mode_changed(self, checked):
        if not checked:
            return
        self._apply_title_mode_visibility()
        mode = self._current_title_mode()
        self.config.set('title_bar_mode', mode)
        self.config.save()
        self.title_changed.emit(self.config.get('global_window_title', ''))

    def _choose_title_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选择标题栏图片', '', '图片 (*.png *.jpg *.jpeg *.bmp *.ico)'
        )
        if not path:
            return
        self.title_image_edit.setText(path)
        self.config.set('global_title_image', path)
        self.config.set('title_bar_mode', 'image')
        self.config.save()
        for radio in self.title_mode_radios:
            radio.blockSignals(True)
        self._set_title_mode_radio('image')
        for radio in self.title_mode_radios:
            radio.blockSignals(False)
        self._apply_title_mode_visibility()
        self.title_changed.emit(self.config.get('global_window_title', ''))

    def _update_background_count(self):
        images = list_background_images()
        settings = get_background_settings()
        if settings.get('enabled', True):
            self.bg_count_label.setText(f'背景图片/视频（{len(images)} 个）')
            return
        self.bg_count_label.setText(f'背景图片/视频（{len(images)} 个，已关闭）')

    def _load_background_ui(self):
        settings = get_background_settings()
        index = self.bg_fit_combo.findData(settings.get('fit', 'auto'))
        self.bg_fit_combo.blockSignals(True)
        self.bg_fit_combo.setCurrentIndex(index if index >= 0 else 0)
        self.bg_fit_combo.blockSignals(False)
        loop_mode = str(settings.get('loop_mode', DEFAULT_LOOP_MODE) or DEFAULT_LOOP_MODE)
        loop_index = self.bg_loop_combo.findData(loop_mode)
        self.bg_loop_combo.blockSignals(True)
        self.bg_loop_combo.setCurrentIndex(loop_index if loop_index >= 0 else self.bg_loop_combo.findData(DEFAULT_LOOP_MODE))
        self.bg_loop_combo.blockSignals(False)
        self.bg_opacity_slider.blockSignals(True)
        self.bg_opacity_slider.setValue(int(settings.get('opacity', 80)))
        self.bg_opacity_slider.blockSignals(False)
        self.bg_opacity_label.setText(f'{self.bg_opacity_slider.value()}%')
        self.bg_blur_slider.blockSignals(True)
        self.bg_blur_slider.setValue(int(settings.get('blur', 0)))
        self.bg_blur_slider.blockSignals(False)
        self.bg_blur_label.setText(str(self.bg_blur_slider.value()))
        self.bg_depth_slider.blockSignals(True)
        self.bg_depth_slider.setValue(int(settings.get('ui_depth', 0)))
        self.bg_depth_slider.blockSignals(False)
        self.bg_depth_label.setText(str(self.bg_depth_slider.value()))
        volume = max(0, min(100, int(self.config.get('background_video_volume', 70) or 70)))
        self.bg_volume_slider.blockSignals(True)
        self.bg_volume_slider.setValue(volume)
        self.bg_volume_slider.blockSignals(False)
        self.bg_volume_label.setText(f'{volume}%')
        self.bg_overlay_check.blockSignals(True)
        self.bg_overlay_check.setChecked(bool(settings.get('overlay', True)))
        self.bg_overlay_check.blockSignals(False)
        self.bg_video_music_check.blockSignals(True)
        self.bg_video_music_check.setChecked(
            bool(self.config.get('background_video_audio', False))
        )
        self.bg_video_music_check.blockSignals(False)
        self.bg_pause_check.blockSignals(True)
        self.bg_pause_check.setChecked(
            bool(self.config.get('background_video_pause_on_background', True))
        )
        self.bg_pause_check.blockSignals(False)
        self.window_title_edit.blockSignals(True)
        self.window_title_edit.setText(self.config.get('global_window_title', 'Spark Craft Launcher') or '')
        self.window_title_edit.blockSignals(False)
        title_color = self.config.get('global_title_color', '#88DDDD') or '#88DDDD'
        self.window_title_color_edit.setText(title_color)
        self.title_color_preview.setStyleSheet(f'background:{title_color}; border-radius:6px; border:1px solid rgba(255,255,255,0.2);')
        title_mode = str(self.config.get('title_bar_mode', 'text') or 'text')
        for radio in self.title_mode_radios:
            radio.blockSignals(True)
        self._set_title_mode_radio(title_mode)
        for radio in self.title_mode_radios:
            radio.blockSignals(False)
        self.title_image_edit.setText(str(self.config.get('global_title_image', '') or ''))
        self._apply_title_mode_visibility()
        self._update_background_count()

    def _on_theme_selected(self, theme):
        theme = str(theme or 'system')
        if theme not in self.theme_buttons:
            theme = 'system'
        for key, button in self.theme_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == theme)
            button.blockSignals(False)
        self.config.set('theme', theme, auto_save=False)
        self.config.set('theme_user_set', True, auto_save=False)
        self.config.save()
        self._theme_apply_token = getattr(self, '_theme_apply_token', 0) + 1
        token = self._theme_apply_token
        QTimer.singleShot(30, lambda t=str(theme), k=token: self._apply_theme_delayed(t, k))

    def _apply_theme_delayed(self, theme, token):
        if token != getattr(self, '_theme_apply_token', 0):
            return
        self.theme_changed.emit(str(theme))

    def _on_video_music_toggled(self, checked):
        self.config.set('background_video_audio', bool(checked))
        self.config.save()
        self.video_music_changed.emit(bool(checked))

    def _on_video_pause_toggled(self, checked):
        self.config.set('background_video_pause_on_background', bool(checked))
        self.config.save()

    def _on_music_pause_toggled(self, checked):
        self.config.set('background_music_pause_on_background', bool(checked))
        self.config.save()

    def _on_music_autoplay_toggled(self, checked):
        self.config.set('background_music_autoplay', bool(checked))
        self.config.save()

    def _on_video_volume_changed(self, value):
        value = max(0, min(100, int(value)))
        self.bg_volume_label.setText(f'{value}%')
        self.config.set('background_video_volume', value)
        self.config.save()
        self.video_volume_changed.emit(value)

    def set_video_music_checked(self, checked):
        self.bg_video_music_check.setChecked(bool(checked))

    def _on_background_setting_changed(self):
        self.bg_opacity_label.setText(f'{self.bg_opacity_slider.value()}%')
        self.bg_blur_label.setText(str(self.bg_blur_slider.value()))
        self.bg_depth_label.setText(str(self.bg_depth_slider.value()))
        self._bg_timer.start()
        if not self._bg_preview_timer.isActive():
            self._bg_preview_timer.start()

    def _on_background_loop_mode_changed(self, _index=None):
        mode = self.bg_loop_combo.currentData() or DEFAULT_LOOP_MODE
        save_background_setting('background_loop_mode', mode)
        save_background_setting('background_enabled', True)
        self._update_background_count()
        self.background_refresh.emit()

    def _background_values(self):
        return {'background_fit': self.bg_fit_combo.currentData() or 'auto', 'background_opacity': self.bg_opacity_slider.value(), 'background_blur': self.bg_blur_slider.value(), 'background_ui_depth': self.bg_depth_slider.value(), 'background_overlay': self.bg_overlay_check.isChecked()}

    def _preview_background_settings(self):
        for key, value in self._background_values().items():
            self.config.set(key, value, auto_save=False)
        self.background_changed.emit()

    def _apply_background_settings(self):
        for key, value in self._background_values().items():
            save_background_setting(key, value)
        save_background_setting('background_enabled', True)
        self._update_background_count()
        self.background_changed.emit()

    def _open_background_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(pictures_dir())))

    def _refresh_background_image(self):
        save_background_setting('background_enabled', True)
        next_image()
        self._update_background_count()
        self.background_refresh.emit()
        self.background_changed.emit()

    def _clear_background_images(self):
        save_background_setting('background_enabled', False)
        save_background_setting('background_index', 0)
        self._update_background_count()
        self.background_changed.emit()

    def _load_music_ui(self):
        player = BackgroundMusicPlayer.instance()
        volume = player.volume()
        self.music_volume_slider.blockSignals(True)
        self.music_volume_slider.setValue(volume)
        self.music_volume_slider.blockSignals(False)
        self.music_volume_label.setText(f'{volume}%')
        self.music_autoplay_check.blockSignals(True)
        self.music_autoplay_check.setChecked(
            bool(self.config.get('background_music_autoplay', True))
        )
        self.music_autoplay_check.blockSignals(False)
        self.music_pause_check.blockSignals(True)
        self.music_pause_check.setChecked(
            bool(self.config.get('background_music_pause_on_background', True))
        )
        self.music_pause_check.blockSignals(False)
        if not getattr(self, '_music_state_connected', False):
            player.state_changed.connect(self._update_music_count)
            self._music_state_connected = True
        self._update_music_count()

    def _update_music_count(self):
        if not hasattr(self, 'music_count_label'):
            return
        files = list_music_files()
        text = f'音乐文件（{len(files)} 首）'
        try:
            current = BackgroundMusicPlayer.instance().current_name()
        except Exception:
            current = ''
        if current:
            text += f' · 当前：{current}'
        self.music_count_label.setText(text)

    def _on_music_volume_changed(self, value: int):
        self.music_volume_label.setText(f'{value}%')
        try:
            BackgroundMusicPlayer.instance().set_volume(value)
        except Exception:
            pass

    def _refresh_music_files(self):
        try:
            player = BackgroundMusicPlayer.instance()
            player.reload(play=not player.user_paused())
        except Exception:
            pass
        self._update_music_count()

    def _clear_music_playback(self):
        try:
            BackgroundMusicPlayer.instance().clear_playback()
        except Exception:
            pass
        self._update_music_count()

    def _open_music_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(music_dir())))

    def _load_community_ui(self):
        for combo, key, default in (self.community_source_combo, 'community_source_pref', 'official'), (self.mod_filename_combo, 'mod_filename_format', 'bracket_title'), (self.mod_manage_combo, 'mod_manage_style', 'title_translated'):
            idx = combo.findData(self.config.get(key, default))
            combo.blockSignals(True)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _save_community_settings(self):
        self.config.set('community_source_pref', self.community_source_combo.currentData() or 'official')
        self.config.set('mod_filename_format', self.mod_filename_combo.currentData() or 'bracket_title')
        self.config.set('mod_manage_style', self.mod_manage_combo.currentData() or 'title_translated')
        self.settings_changed.emit()

    def _auto_save(self):
        '''保存除语言之外的其它设置（Java、内存、镜像等）'''

        current_index = self.java_combo.currentIndex()
        if current_index == 0:
            self.config.set('java_path', '', auto_save=False)
        else:
            current_path = self.java_combo.currentData()
            if current_path and Path(current_path).exists():
                self.config.set('java_path', current_path, auto_save=False)
        self.config.set('file_download_source', self.file_source_combo.currentData(), auto_save=False)
        self.config.set('version_list_source', self.version_source_combo.currentData(), auto_save=False)
        self.config.set('download_speed_limit', 0.0 if self.speed_limit_slider.value() >= 1000 else max(0.1, self.speed_limit_slider.value() / 10.0), auto_save=False)
        self.config.set('mirror', self.file_source_combo.currentData(), auto_save=False)
        self.config.save()
        self.settings_changed.emit()


