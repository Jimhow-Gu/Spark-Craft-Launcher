'''整合包导入页面（内嵌页面，不使用二级窗口）。'''

from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)
from ui.widgets.rounded_combo import RoundedComboBox
from core.app_font import ui_font
from ui.widgets.back_button import BackButton
from ui.widgets.smooth_progress import SmoothProgressBar


class ModpackImportPage(QWidget):
    back_requested = Signal()
    start_requested = Signal(str, str)
    def __init__(self, info, path, default_name, parent=None, installed_versions=None):
        super().__init__(parent)
        self.info = info
        self.path = Path(path)
        self.installed_versions = list(installed_versions or [])
        self._running = False
        self._last_mode = 'new'
        self.setObjectName('ModpackImportPage')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet('QWidget#ModpackImportPage { background:transparent; }')
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(14)
        top = QHBoxLayout()
        back = BackButton()
        back.clicked.connect(self._on_back)
        self.back_btn = back
        title = QLabel('导入整合包')
        title.setFont(ui_font(20, QFont.DemiBold))
        title.setStyleSheet('color:#f0f0f0; font-size:20px; font-weight:900;')
        top.addWidget(back)
        top.addWidget(title)
        top.addStretch(1)
        root.addLayout(top)
        card = QFrame()
        card.setObjectName('ModpackInfoCard')
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet('QFrame#ModpackInfoCard { background:rgba(30,32,40,0.9); border:1px solid rgba(255,255,255,0.22); border-radius:10px; } QLabel { background:transparent; }')
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(8)
        name = QLabel(info.name or self.path.stem)
        name.setStyleSheet('color:#f0f0f0; font-size:17px; font-weight:900;')
        card_layout.addWidget(name)
        loader = f'{info.loader_id or "原版"} {info.loader_version}'.strip()
        details = QLabel(f'Minecraft：{info.mc_version}\n加载器：{loader}\n文件数：{info.file_count}\n来源：{self.path}')
        details.setWordWrap(True)
        details.setStyleSheet('color:rgba(255,255,255,0.62); font-size:13px;')
        card_layout.addWidget(details)
        root.addWidget(card)

        mode_row = QHBoxLayout()
        mode_label = QLabel('安装方式')
        mode_label.setFixedWidth(70)
        mode_label.setStyleSheet('color:rgba(255,255,255,0.72);')
        self.new_radio = QRadioButton('新建实例')
        self.existing_radio = QRadioButton('安装到现有实例')
        self.new_radio.setChecked(True)
        self.new_radio.setStyleSheet('QRadioButton { color:#f0f0f0; spacing:6px; }')
        self.existing_radio.setStyleSheet('QRadioButton { color:#f0f0f0; spacing:6px; }')
        if not self.installed_versions:
            self.existing_radio.setEnabled(False)
            self.existing_radio.setToolTip('当前游戏目录没有可安装的已存在版本')
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.new_radio)
        self.mode_group.addButton(self.existing_radio)
        self.new_radio.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.new_radio)
        mode_row.addWidget(self.existing_radio)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        self.existing_widget = QWidget()
        existing_row = QHBoxLayout(self.existing_widget)
        existing_row.setContentsMargins(0, 0, 0, 0)
        existing_row.setSpacing(8)
        target_label = QLabel('目标版本')
        target_label.setFixedWidth(70)
        target_label.setStyleSheet('color:rgba(255,255,255,0.72);')
        self.existing_combo = RoundedComboBox()
        self.existing_combo.setMinimumHeight(36)
        self.existing_combo.setStyleSheet(
            'QComboBox { background:rgba(255,255,255,0.06); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.14); border-radius:7px;'
            ' padding:5px 10px; }'
            ''
        )
        for item in self.installed_versions:
            if isinstance(item, dict):
                version_id = str(item.get('id') or '').strip()
                item_loader = str(item.get('loader') or '').strip()
            else:
                version_id = str(item or '').strip()
                item_loader = ''
            if not version_id:
                continue
            label = version_id + (f'  ·  {item_loader}' if item_loader else '')
            self.existing_combo.addItem(label, version_id)
        existing_row.addWidget(target_label)
        existing_row.addWidget(self.existing_combo, 1)
        self.existing_widget.hide()
        root.addWidget(self.existing_widget)

        name_row = QHBoxLayout()
        self.name_label = QLabel('版本名称')
        self.name_label.setFixedWidth(70)
        self.name_label.setStyleSheet('color:rgba(255,255,255,0.72);')
        self.name_edit = QLineEdit(default_name)
        self.name_edit.setMinimumHeight(36)
        self.name_edit.setPlaceholderText('导入后显示的版本名称')
        self.name_edit.setStyleSheet('QLineEdit { background:rgba(255,255,255,0.06); color:#f0f0f0; border:1px solid rgba(255,255,255,0.14); border-radius:7px; padding:5px 10px; }')
        self.name_edit.returnPressed.connect(self._on_start)
        name_row.addWidget(self.name_label)
        name_row.addWidget(self.name_edit, 1)
        root.addLayout(name_row)
        self.error_label = QLabel('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('color:#ff7777; font-size:12px;')
        self.error_label.hide()
        root.addWidget(self.error_label)
        self.progress = SmoothProgressBar()
        self.progress.setRange(0, 100)
        self.progress.reset_value()
        self.progress.hide()
        self.progress.setStyleSheet('QProgressBar { background:rgba(255,255,255,0.12); border-radius:5px; height:12px; }QProgressBar::chunk { background:#88DDDD; border-radius:5px; }')
        root.addWidget(self.progress)
        self.status_label = QLabel('将自动启用版本隔离，不会影响其他版本。')
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px;')
        root.addWidget(self.status_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton('取消导入')
        self.cancel_btn.setStyleSheet('QPushButton { background:#5a3030; color:white; border:none; border-radius:6px; padding:7px 16px; }')
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.hide()
        self.start_btn = QPushButton('开始导入')
        self.start_btn.setStyleSheet('QPushButton { background:#90D9D9; color:#122828; border:none; border-radius:6px; padding:7px 22px; }QPushButton:hover { background:#78CACA; }QPushButton:disabled { background:#333; color:#777; }')
        self.start_btn.clicked.connect(self._on_start)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.start_btn)
        root.addLayout(buttons)
        root.addStretch(1)

    def _on_mode_changed(self, checked=False):
        if self._running:
            return
        existing = bool(self.existing_radio.isChecked())
        self._last_mode = 'existing' if existing else 'new'
        self.existing_widget.setVisible(existing)
        self.name_edit.setEnabled(not existing)
        self.existing_combo.setEnabled(existing)
        self.start_btn.setText('开始安装' if existing else '开始导入')
        if existing:
            self.status_label.setText('将安装到现有实例并启用版本隔离，不会删除已有文件。')
        else:
            self.status_label.setText('将自动启用版本隔离，不会影响其他版本。')

    def _on_back(self):
        if not self._running:
            self.back_requested.emit()

    def _on_start(self):
        if self._running:
            return
        if self.existing_radio.isChecked():
            version_id = str(self.existing_combo.currentData() or '').strip()
            if not version_id:
                self.show_error('请选择要安装到的现有实例')
                return
            self._last_mode = 'existing'
            self.start_requested.emit('existing', version_id)
            return
        name = self.name_edit.text().strip()
        if not name:
            self.show_error('版本名称不能为空')
            return
        self._last_mode = 'new'
        self.start_requested.emit('new', name)

    def _on_cancel(self):
        self.status_label.setText('正在取消导入...')
        self.cancel_btn.setEnabled(False)
        self.back_requested.emit()

    def set_running(self, running):
        self._running = running
        self.name_edit.setEnabled(not running and not self.existing_radio.isChecked())
        self.existing_combo.setEnabled(not running and self.existing_radio.isChecked())
        self.new_radio.setEnabled(not running)
        self.existing_radio.setEnabled(not running and bool(self.installed_versions))
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setVisible(running)
        self.cancel_btn.setEnabled(running)
        self.back_btn.setEnabled(not running)
        if running:
            self.progress.reset_value()
            self.progress.show()
            self.error_label.hide()

    def set_status(self, text):
        self.status_label.setText(text)

    def set_progress(self, value):
        self.progress.set_animated_value(max(0, min(100, int(value))))

    def show_error(self, message):
        self._running = False
        self.start_btn.setEnabled(True)
        self.new_radio.setEnabled(True)
        self.existing_radio.setEnabled(bool(self.installed_versions))
        is_existing = self.existing_radio.isChecked()
        self.name_edit.setEnabled(not is_existing)
        self.existing_combo.setEnabled(is_existing)
        self.cancel_btn.hide()
        self.back_btn.setEnabled(True)
        self.error_label.setText(message)
        self.error_label.show()
        self.status_label.setText('导入失败')

    def show_success(self, version_id, mode=None):
        self._running = False
        self.progress.set_animated_value(100)
        self.progress.show()
        self.start_btn.hide()
        self.cancel_btn.hide()
        self.back_btn.setEnabled(True)
        self.name_edit.setEnabled(False)
        self.existing_combo.setEnabled(False)
        self.new_radio.setEnabled(False)
        self.existing_radio.setEnabled(False)
        self.error_label.hide()
        mode = mode or self._last_mode
        if mode == 'existing':
            self.status_label.setText(
                f'整合包已安装到现有版本：{version_id}（已启用版本隔离）'
            )
        else:
            self.status_label.setText(
                f'整合包已导入为版本：{version_id}（已启用版本隔离）'
            )
