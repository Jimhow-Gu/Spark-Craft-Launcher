'''高级启动选项面板（全局设置 / 版本设置共用）'''

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QLineEdit, QComboBox, QCheckBox, QPushButton)
from ui.widgets.themed_checkbox import ThemedCheckBox
from core.jvm import DEFAULT_JVM_ARGS, MEMORY_GC_OPTIONS
from core.launch import LaunchManager
from core.java_scan import JavaScanService

class AdvancedOptionsPanel(QWidget):
    def __init__(self, getter, setter, show_java=False, parent=None):
        super().__init__(parent)
        self._getter = getter
        self._setter = setter
        self._show_java = show_java
        self._loading = False
        self._java_scan_connected = False
        self._init_ui()
        self.reload()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._label('Java 虚拟机参数'))
        self.jvm_edit = QPlainTextEdit()
        self.jvm_edit.setMinimumHeight(80)
        self.jvm_edit.setStyleSheet('\n            QPlainTextEdit {\n                background: rgba(255,255,255,0.05);\n                border: 1px solid rgba(255,255,255,0.12);\n                border-radius: 6px; color: #f0f0f0;\n                font-family: Consolas, monospace; font-size: 12px;\n                padding: 6px;\n            }\n        ')
        self._jvm_timer = QTimer(self)
        self._jvm_timer.setSingleShot(True)
        self._jvm_timer.setInterval(500)
        self._jvm_timer.timeout.connect(self._save_jvm)
        self.jvm_edit.textChanged.connect((lambda: self._jvm_timer.start()))
        layout.addWidget(self.jvm_edit)
        layout.addWidget(self._label('游戏参数'))
        self.game_edit = QLineEdit()
        self.game_edit.setPlaceholderText('追加到游戏启动参数中，例如 --fullscreen')
        self.game_edit.setStyleSheet(self._line_style())
        self.game_edit.editingFinished.connect(self._save_game)
        layout.addWidget(self.game_edit)
        layout.addWidget(self._label('启动前执行命令'))
        self.pre_edit = QLineEdit()
        self.pre_edit.setPlaceholderText('可选，例如 taskkill /F /IM somehelper.exe')
        self.pre_edit.setStyleSheet(self._line_style())
        self.pre_edit.editingFinished.connect(self._save_pre)
        layout.addWidget(self.pre_edit)
        if self._show_java:
            java_row = QHBoxLayout()
            java_row.addWidget(self._label('Java 列表'))
            self.java_combo = QComboBox()
            self.java_combo.setStyleSheet(self._line_style())
            self.java_combo.currentIndexChanged.connect(self._save_java)
            java_row.addWidget(self.java_combo, 1)
            refresh = QPushButton('刷新')
            refresh.setStyleSheet('QPushButton { background:#3a3a3a; color:white; border:none; border-radius:6px; padding:6px 12px; } QPushButton:hover { background:#4a4a4a; }')
            refresh.clicked.connect(self._reload_java)
            java_row.addWidget(refresh)
            layout.addLayout(java_row)
        else:
            self.java_combo = None
        gc_row = QHBoxLayout()
        gc_row.addWidget(self._label('内存管理'))
        self.gc_combo = QComboBox()
        self.gc_combo.setStyleSheet(self._line_style())
        for key, label in MEMORY_GC_OPTIONS:
            self.gc_combo.addItem(label, key)
        self.gc_combo.currentIndexChanged.connect(self._save_gc)
        gc_row.addWidget(self.gc_combo, 1)
        layout.addLayout(gc_row)
        self.mod_update_check = ThemedCheckBox('禁止更新Mod')
        self.file_verify_check = ThemedCheckBox('关闭文件校验')
        self.wrapper_check = ThemedCheckBox('禁用 Java Launch Wrapper')
        self.lwjgl_check = ThemedCheckBox('禁用 LWJGL Unsafe Agent')
        self.gpu_check = ThemedCheckBox('使用高性能显卡')
        self.ssl_check = ThemedCheckBox('在正版登录时验证 SSL 证书')
        for box in self.mod_update_check, self.file_verify_check, self.wrapper_check, self.lwjgl_check, self.gpu_check, self.ssl_check:
            layout.addWidget(box)
        self.mod_update_check.toggled.connect((lambda v: self._set('disable_mod_update', v)))
        self.file_verify_check.toggled.connect((lambda v: self._set('disable_file_verification', v)))
        self.wrapper_check.toggled.connect((lambda v: self._set('disable_java_launch_wrapper', v)))
        self.lwjgl_check.toggled.connect((lambda v: self._set('disable_lwjgl_unsafe_agent', v)))
        self.gpu_check.toggled.connect((lambda v: self._set('use_high_performance_gpu', v)))
        self.ssl_check.toggled.connect((lambda v: self._set('verify_ssl', v)))

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setStyleSheet('color: rgba(255,255,255,0.75); font-size: 13px;')
        return label

    @staticmethod
    def _line_style():
        return '\n            QLineEdit, QComboBox {\n                background: rgba(255,255,255,0.05);\n                border: 1px solid rgba(255,255,255,0.12);\n                border-radius: 6px; color: #f0f0f0; padding: 5px 8px;\n            }\n            QComboBox::drop-down { border: none; }\n            QComboBox QAbstractItemView { background: #2a2a2a; color: #f0f0f0; }\n        '

    def reload(self):
        self._loading = True
        self.jvm_edit.setPlainText(str(self._getter('jvm_args', DEFAULT_JVM_ARGS) or DEFAULT_JVM_ARGS))
        self.game_edit.setText(str(self._getter('game_args', '') or ''))
        self.pre_edit.setText(str(self._getter('pre_launch_command', '') or ''))
        gc = str(self._getter('memory_gc', 'tuned_g1') or 'tuned_g1')
        idx = self.gc_combo.findData(gc)
        self.gc_combo.setCurrentIndex(idx if idx >= 0 else self.gc_combo.findData('tuned_g1'))
        self.mod_update_check.setChecked(bool(self._getter('disable_mod_update', False)))
        self.file_verify_check.setChecked(bool(self._getter('disable_file_verification', False)))
        self.wrapper_check.setChecked(bool(self._getter('disable_java_launch_wrapper', False)))
        self.lwjgl_check.setChecked(bool(self._getter('disable_lwjgl_unsafe_agent', False)))
        self.gpu_check.setChecked(bool(self._getter('use_high_performance_gpu', True)))
        self.ssl_check.setChecked(bool(self._getter('verify_ssl', False)))
        self._loading = False
        if self._show_java and self.java_combo is not None:
            self._reload_java()

    def _reload_java(self):
        if self.java_combo is None:
            return
        self._loading = True
        current = str(self._getter('java_path', '') or '')
        self.java_combo.clear()
        self.java_combo.addItem('自动选择最佳 Java', '')
        java_list = LaunchManager().peek_cached_java_list()
        if java_list is None:
            java_list = []
            if not self._java_scan_connected:
                self._java_scan_connected = True
                JavaScanService.instance().finished.connect(self._on_java_scan_finished)
        try:
            for ver, path in java_list:
                self.java_combo.addItem(f'Java {ver}  |  {path}', path)
        except Exception:
            pass
        if current:
            idx = self.java_combo.findData(current)
            if idx < 0:
                self.java_combo.addItem(f'自定义: {current}', current)
                idx = self.java_combo.count() - 1
            self.java_combo.setCurrentIndex(idx)
        else:
            self.java_combo.setCurrentIndex(0)
        self._loading = False

    def _on_java_scan_finished(self, java_list):
        self._reload_java()

    def _set(self, key, value):
        if self._loading:
            return
        self._setter(key, value)

    def _save_jvm(self):
        self._set('jvm_args', self.jvm_edit.toPlainText().strip())

    def _save_game(self):
        self._set('game_args', self.game_edit.text().strip())

    def _save_pre(self):
        self._set('pre_launch_command', self.pre_edit.text().strip())

    def _save_gc(self):
        self._set('memory_gc', self.gc_combo.currentData() or 'tuned_g1')

    def _save_java(self):
        if self._loading or self.java_combo is None:
            return
        self._set('java_path', self.java_combo.currentData() or '')


