'''启动失败/崩溃提示对话框，支持导出日志 zip'''

from pathlib import Path
from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QTextEdit, QFileDialog, QMessageBox)
from core.crash import CrashReport, export_log_zip
from ui.widgets.frameless_dialog import FramelessDialog

class CrashDialog(FramelessDialog):
    def __init__(self, report: CrashReport, log_path: Optional[Path]=None, extra_files: Optional[List[Path]]=None, parent=None):
        super().__init__(parent, '游戏启动失败')
        self.report = report
        self.log_path = Path(log_path) if log_path else None
        self.extra_files = extra_files or []
        self.setMinimumSize(640, 420)
        layout = self.content_layout
        layout.setContentsMargins(20, 6, 20, 16)
        layout.setSpacing(10)
        title = QLabel('游戏启动失败')
        title.setStyleSheet('color:#ff7777; font-size:18px; font-weight:900;')
        layout.addWidget(title)
        message = QLabel(report.message())
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        message.setStyleSheet('color:rgba(255,255,255,0.85); font-size:13px;')
        layout.addWidget(message)
        if report.detail:
            detail = QTextEdit()
            detail.setReadOnly(True)
            detail.setPlainText(report.detail)
            detail.setStyleSheet('\n                QTextEdit { background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1);\n                            border-radius:6px; color:rgba(255,255,255,0.7);\n                            font-size:11px; padding:6px; }\n            ')
            layout.addWidget(detail, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        export_btn = QPushButton('导出日志')
        export_btn.setStyleSheet('\n            QPushButton { background:#f0a030; color:white; border:none;\n                          border-radius:6px; padding:8px 18px; }\n            QPushButton:hover { background:#d08020; }\n        ')
        export_btn.clicked.connect(self._export)
        ok_btn = QPushButton('确定')
        ok_btn.setStyleSheet('\n            QPushButton { background:#0078d4; color:white; border:none;\n                          border-radius:6px; padding:8px 18px; }\n            QPushButton:hover { background:#106ebe; }\n        ')
        ok_btn.clicked.connect(self.accept)
        row.addWidget(export_btn)
        row.addWidget(ok_btn)
        layout.addLayout(row)

    def _export(self):
        default = f'crash-{self.report.version_name}-{int(__import__("time").time())}.zip'
        path, _ = QFileDialog.getSaveFileName(self, '导出日志', default, 'ZIP 压缩包 (*.zip)')
        if not path:
            return
        try:
            export_log_zip(self.log_path, self.report.version_name, Path(path), self.extra_files)
            QMessageBox.information(self, '导出成功', f'日志已导出到：\n{path}')
        except Exception as exc:
            QMessageBox.critical(self, '导出失败', str(exc))
            return


