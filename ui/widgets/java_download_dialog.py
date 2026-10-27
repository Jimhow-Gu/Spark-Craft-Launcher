'''启动器内置 Java 下载进度对话框。'''

from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

from core.java_download import JavaDownloadWorker
from ui.widgets.smooth_progress import RoundedProgressBar


class JavaDownloadDialog(QDialog):
    def __init__(self, parent, feature):
        super().__init__(parent)
        self.feature = int(feature)
        self.java_path = ''
        self.error = ''
        self._worker = None
        self.setWindowTitle(f'下载 Java {self.feature}')
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(
            'QDialog { background:#1f2229; }'
            'QLabel { color:#f0f0f0; background:transparent; }'
            'QProgressBar { background:rgba(255,255,255,0.12); border:none; border-radius:6px; height:12px; text-align:center; color:transparent; }'
            'QProgressBar::chunk { background:#0078d4; border-radius:6px; }'
            'QPushButton { background:#3a3d47; color:#f0f0f0; border:none; border-radius:7px; padding:7px 18px; }'
            'QPushButton:hover { background:#4a4d57; }'
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        title = QLabel(f'正在下载并安装 Java {self.feature}')
        title.setStyleSheet('font-size:16px; font-weight:600;')
        layout.addWidget(title)
        self.status_label = QLabel(f'正在查找 Java {self.feature} 下载地址...')
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet('color:rgba(255,255,255,0.62);')
        layout.addWidget(self.status_label)
        self.progress = RoundedProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12)
        layout.addWidget(self.progress)
        self.error_label = QLabel('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('color:#ff8a8a; font-size:12px;')
        self.error_label.hide()
        layout.addWidget(self.error_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.clicked.connect(self.reject)
        self.close_btn = QPushButton('关闭')
        self.close_btn.setStyleSheet('QPushButton { background:#0078d4; color:white; }')
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.hide()
        row.addWidget(self.cancel_btn)
        row.addWidget(self.close_btn)
        layout.addLayout(row)
        self._worker = JavaDownloadWorker(self.feature, self)
        self._worker.status.connect(self.status_label.setText)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, done, total):
        if total > 0:
            self.progress.setValue(max(0, min(1000, int(done * 1000 / total))))
            self.status_label.setText(f'正在下载 Java {self.feature}：{done * 100 // total}%')
        else:
            self.progress.setValue((self.progress.value() + 23) % 1001)
            self.status_label.setText('正在下载 Java...')

    def _on_done(self, path):
        self.java_path = str(path)
        self.progress.setValue(1000)
        self.status_label.setText(f'Java {self.feature} 已安装完成')
        self.cancel_btn.hide()
        self.close_btn.show()
        self.accept()

    def _on_failed(self, message):
        self.error = str(message)
        self.error_label.setText(str(message))
        self.error_label.show()
        self.status_label.setText('Java 下载失败')
        self.cancel_btn.hide()
        self.close_btn.show()

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
        self._cancel_worker()
        super().reject()

    def closeEvent(self, event):
        self._cancel_worker()
        super().closeEvent(event)


__all__ = ['JavaDownloadDialog']
