'''全局设置中的离线皮肤配置。'''

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)


class OfflineSkinSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        desc = QLabel('离线登录可通过 authlib-injector 使用皮肤；默认每次启动随机选择 Steve 或 Alex。')
        desc.setWordWrap(True)
        desc.setStyleSheet('color:rgba(255,255,255,0.5); font-size:12px;')
        layout.addWidget(desc)

        combo_style = (
            'QComboBox { background:rgba(255,255,255,0.05);'
            ' border:1px solid rgba(255,255,255,0.12); border-radius:6px;'
            ' color:#f0f0f0; padding:4px 8px; }'
            'QComboBox::drop-down { border:none; }'
            'QComboBox QAbstractItemView { background:#2a2a2a; color:#f0f0f0; }'
        )
        edit_style = (
            'QLineEdit { background:rgba(255,255,255,0.05);'
            ' border:1px solid rgba(255,255,255,0.12); border-radius:6px;'
            ' color:#f0f0f0; padding:4px 8px; }'
        )

        row = QHBoxLayout()
        self.skin_mode_combo = QComboBox()
        self.skin_mode_combo.addItem('随机（Steve / Alex）', 'random')
        self.skin_mode_combo.addItem('Steve', 'steve')
        self.skin_mode_combo.addItem('Alex', 'alex')
        self.skin_mode_combo.addItem('正版玩家名皮肤', 'premium')
        self.skin_mode_combo.addItem('本地皮肤文件', 'custom')
        self.skin_mode_combo.setStyleSheet(combo_style)
        self.skin_mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row.addWidget(QLabel('皮肤来源'))
        row.addWidget(self.skin_mode_combo, 1)
        self.skin_model_combo = QComboBox()
        self.skin_model_combo.addItem('经典 (Steve)', 'classic')
        self.skin_model_combo.addItem('纤细 (Alex)', 'slim')
        self.skin_model_combo.setStyleSheet(combo_style)
        self.skin_model_combo.currentIndexChanged.connect(self._save_settings)
        row.addWidget(QLabel('模型'))
        row.addWidget(self.skin_model_combo)
        layout.addLayout(row)

        self.skin_premium_row = QWidget()
        premium_layout = QHBoxLayout(self.skin_premium_row)
        premium_layout.setContentsMargins(0, 0, 0, 0)
        self.skin_name_edit = QLineEdit()
        self.skin_name_edit.setPlaceholderText('输入正版玩家名，例如 Notch')
        self.skin_name_edit.setStyleSheet(edit_style)
        self.skin_name_edit.editingFinished.connect(self._save_settings)
        premium_layout.addWidget(QLabel('正版玩家名'))
        premium_layout.addWidget(self.skin_name_edit, 1)
        layout.addWidget(self.skin_premium_row)

        self.skin_custom_row = QWidget()
        custom_layout = QHBoxLayout(self.skin_custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.skin_path_edit = QLineEdit()
        self.skin_path_edit.setPlaceholderText('选择 64x64 PNG 皮肤文件')
        self.skin_path_edit.setStyleSheet(edit_style)
        self.skin_path_edit.editingFinished.connect(self._save_settings)
        choose_skin_btn = QPushButton('选择文件')
        choose_skin_btn.setStyleSheet(
            'QPushButton { background:#3a3a3a; color:white; border:none;'
            ' border-radius:6px; padding:6px 12px; }'
            'QPushButton:hover { background:#4a4a4a; }'
        )
        choose_skin_btn.clicked.connect(self._choose_skin_file)
        custom_layout.addWidget(QLabel('皮肤文件'))
        custom_layout.addWidget(self.skin_path_edit, 1)
        custom_layout.addWidget(choose_skin_btn)
        layout.addWidget(self.skin_custom_row)

        self.load_settings()

    def load_settings(self):
        try:
            from core.skin import SkinManager
            settings = SkinManager().get_settings()
        except Exception:
            settings = {'mode': 'random', 'premium_name': '', 'model': 'classic', 'custom_path': ''}
        self.skin_mode_combo.blockSignals(True)
        index = self.skin_mode_combo.findData(settings.get('mode', 'random'))
        self.skin_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self.skin_mode_combo.blockSignals(False)
        self.skin_name_edit.setText(str(settings.get('premium_name') or ''))
        self.skin_path_edit.setText(str(settings.get('custom_path') or ''))
        self.skin_model_combo.blockSignals(True)
        model_index = self.skin_model_combo.findData(settings.get('model', 'classic'))
        self.skin_model_combo.setCurrentIndex(model_index if model_index >= 0 else 0)
        self.skin_model_combo.blockSignals(False)
        self._update_row_visibility()

    def _settings(self):
        return {
            'mode': self.skin_mode_combo.currentData() or 'random',
            'premium_name': self.skin_name_edit.text().strip(),
            'model': self.skin_model_combo.currentData() or 'classic',
            'custom_path': self.skin_path_edit.text().strip(),
        }

    def _update_row_visibility(self):
        mode = self.skin_mode_combo.currentData()
        self.skin_premium_row.setVisible(mode == 'premium')
        self.skin_custom_row.setVisible(mode == 'custom')
        self.skin_model_combo.setEnabled(mode in ('premium', 'custom'))

    def _on_mode_changed(self):
        self._update_row_visibility()
        self._save_settings()

    def _save_settings(self):
        try:
            from core.skin import SkinManager, detect_skin_model
            settings = self._settings()
            if settings['mode'] == 'custom' and settings['custom_path']:
                path = Path(settings['custom_path'])
                if path.is_file():
                    detected = detect_skin_model(str(path))
                    model_index = self.skin_model_combo.findData(detected)
                    if model_index >= 0 and self.skin_model_combo.currentIndex() != model_index:
                        self.skin_model_combo.blockSignals(True)
                        self.skin_model_combo.setCurrentIndex(model_index)
                        self.skin_model_combo.blockSignals(False)
                    settings['model'] = detected
            SkinManager().save_settings(settings)
        except Exception:
            pass

    def _choose_skin_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择皮肤文件', '', 'PNG 皮肤 (*.png)')
        if not path:
            return
        try:
            from core.skin import skin_size_error
            error = skin_size_error(path)
        except Exception:
            error = ''
        if error:
            QMessageBox.warning(self, '皮肤文件不可用', error)
            return
        self.skin_path_edit.setText(path)
        self.skin_mode_combo.setCurrentIndex(self.skin_mode_combo.findData('custom'))
        self._save_settings()

    def shutdown(self):
        # 预览已移除，无需等待皮肤预览线程。
        pass
