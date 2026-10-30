'''发现新版本对话框：展示 Release 信息并下载更新附件。'''

from __future__ import annotations

import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

from core.update import UpdateDownloadWorker
from ui.widgets.smooth_progress import RoundedProgressBar


class UpdateDialog(QDialog):
    def __init__(self, parent, info, kind='version'):
        super().__init__(parent)
        self.info = dict(info or {})
        self.update_kind = str(kind or 'version')
        self.asset_path = ''
        self.error = ''
        self._worker = None
        self.setWindowTitle('发现修复更新' if self.update_kind == 'dll' else '发现新版本')
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(
            'QDialog { background:#1f2229; }'
            'QLabel { color:#f0f0f0; background:transparent; }'
            'QProgressBar { background:rgba(255,255,255,0.12); border:none; border-radius:6px; height:12px; }'
            'QProgressBar::chunk { background:#0078d4; border-radius:6px; }'
            'QPushButton { background:#3a3d47; color:#f0f0f0; border:none; border-radius:7px; padding:7px 18px; }'
            'QPushButton:hover { background:#4a4d57; }'
            'QPushButton:disabled { background:#30333b; color:#858a94; }'
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        title = QLabel(
            '修复了一些bug' if self.update_kind == 'dll'
            else f'发现新版本 {self.info.get("tag") or self.info.get("version") or ""}'
        )
        title.setStyleSheet('font-size:17px; font-weight:700;')
        layout.addWidget(title)
        name = QLabel(str(self.info.get('name') or ''))
        name.setStyleSheet('color:rgba(255,255,255,0.72); font-size:13px;')
        layout.addWidget(name)
        notes = str(self.info.get('body') or '').strip()
        notes = re.sub(r'(?im)^\s*(?:DLL[\s_-]*BUILD|DLL版号)\s*[:=：].*$', '', notes).strip()
        if notes:
            body = QLabel(notes[:1500])
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setStyleSheet('color:rgba(255,255,255,0.58); font-size:12px;')
            body.setMaximumHeight(180)
            layout.addWidget(body)
        self.status_label = QLabel(
            '修复了一些bug，是否下载并安装？' if self.update_kind == 'dll'
            else '是否下载并安装此更新？'
        )
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet('color:rgba(255,255,255,0.62); font-size:12px;')
        layout.addWidget(self.status_label)
        self.progress = RoundedProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.error_label = QLabel('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('color:#ff8a8a; font-size:12px;')
        self.error_label.hide()
        layout.addWidget(self.error_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.later_btn = QPushButton('稍后')
        self.later_btn.clicked.connect(self.reject)
        self.download_btn = QPushButton('下载更新')
        self.download_btn.setStyleSheet('QPushButton { background:#0078d4; color:white; }')
        self.download_btn.clicked.connect(self._on_primary_clicked)
        row.addWidget(self.later_btn)
        row.addWidget(self.download_btn)
        layout.addLayout(row)

    def _on_primary_clicked(self):
        if self.asset_path:
            self.accept()
            return
        self._start_download()

    def _start_download(self):
        if self.asset_path:
            self.accept()
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.download_btn.setEnabled(False)
        self.download_btn.setText('下载中')
        self.download_btn.setStyleSheet('QPushButton { background:#30333b; color:#858a94; }')
        self.later_btn.setText('取消')
        self.status_label.setText('正在连接中国大陆加速线路...')
        self.progress.show()
        self.progress.setValue(0)
        worker = UpdateDownloadWorker(self.info, self)
        self._worker = worker
        worker.status.connect(self.status_label.setText)
        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _on_progress(self, done, total):
        if total > 0:
            value = max(0, min(1000, int(done * 1000 / total)))
            if value > 0:
                value = max(value, 20)
            self.progress.setValue(value)
            self.status_label.setText(f'正在下载更新：{done * 100 // total}%')
        else:
            self.progress.setValue((self.progress.value() + 17) % 1001)
            self.status_label.setText('正在下载更新...')

    def _on_done(self, path):
        self.asset_path = str(path)
        self.progress.setValue(1000)
        self.status_label.setText('下载完成，点击“重启”完成更新。关闭窗口也会在下次启动时替换客户端。')
        self.error_label.hide()
        self.download_btn.setEnabled(True)
        self.download_btn.setText('重启')
        self.download_btn.setStyleSheet('QPushButton { background:#2e7d32; color:white; }')
        self.later_btn.setText('关闭')

    def _on_failed(self, message):
        self.error = str(message)
        self.error_label.setText(str(message))
        self.error_label.show()
        self.progress.hide()
        self.download_btn.setEnabled(True)
        self.download_btn.setText('重试下载')
        self.download_btn.setStyleSheet('QPushButton { background:#0078d4; color:white; }')
        self.later_btn.setText('关闭')

    def _on_finished(self):
        self._worker = None

    def _cancel_worker(self):
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            if not worker.wait(2500):
                worker.terminate()
                worker.wait(500)

    def reject(self):
        if self.asset_path:
            # 下载完成后，关闭窗口同样视为确认更新。
            self.accept()
            return
        self._cancel_worker()
        super().reject()

    def closeEvent(self, event):
        if self.asset_path:
            self.accept()
            event.accept()
            return
        self._cancel_worker()
        super().closeEvent(event)


__all__ = ['UpdateDialog']
