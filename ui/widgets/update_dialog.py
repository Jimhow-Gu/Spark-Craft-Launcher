'''发现新版本对话框：展示 Release 信息并下载更新附件。'''

from __future__ import annotations

import re
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from core.update import UpdateDownloadWorker
from core.paths import scl_dir
from ui.widgets.smooth_progress import RoundedProgressBar


class UpdateDialog(QDialog):
    download_completed = Signal(str, str)
    def __init__(self, parent, info, kind='version'):
        super().__init__(parent)
        self.info = dict(info or {})
        self.update_kind = str(kind or 'version')
        self.asset_path = ''
        self.error = ''
        self._worker = None
        self._primary_state = 'download'
        try:
            self._light = (
                QApplication.palette().color(QPalette.Window).lightness() > 128
            )
        except Exception:
            self._light = False
        self.setWindowTitle('发现修复更新' if self.update_kind == 'dll' else '发现新版本')
        self.setModal(True)
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(24, 20, 24, 0)
        top_layout.setSpacing(12)
        title = QLabel(
            '修复了一些bug' if self.update_kind == 'dll'
            else f'发现新版本 {self.info.get("tag") or self.info.get("version") or ""}'
        )
        title.setStyleSheet('font-size:17px; font-weight:900;')
        top_layout.addWidget(title)
        self.name_label = QLabel(str(self.info.get('name') or ''))
        self.name_label.setStyleSheet('font-size:13px;')
        top_layout.addWidget(self.name_label)
        outer.addWidget(top)
        notes = str(self.info.get('body') or '').strip()
        notes = re.sub(r'(?im)^\s*(?:DLL[\s_-]*BUILD|DLL版号)\s*[:=：].*$', '', notes).strip()
        self.body = None
        if notes:
            self.body = QTextBrowser()
            self.body.setPlainText(notes)
            self.body.setOpenExternalLinks(False)
            self.body.setFrameShape(QFrame.NoFrame)
            self.body.setMinimumHeight(90)
            self.body.setMaximumHeight(190)
            self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.body.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.body.setStyleSheet(
                'QTextBrowser { background:transparent; border:none;'
                ' font-size:12px; }'
            )
            notes_row = QHBoxLayout()
            # 右侧不留边距，让更新说明的滚动条贴到窗口最右侧。
            notes_row.setContentsMargins(24, 12, 0, 0)
            notes_row.setSpacing(0)
            notes_row.addWidget(self.body)
            outer.addLayout(notes_row)
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(24, 12, 24, 18)
        bottom_layout.setSpacing(12)
        self.status_label = QLabel(
            '修复了一些bug，是否下载并安装？' if self.update_kind == 'dll'
            else '是否下载并安装此更新？'
        )
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet('font-size:12px;')
        bottom_layout.addWidget(self.status_label)
        self.progress = RoundedProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12)
        self.progress.hide()
        bottom_layout.addWidget(self.progress)
        self.error_label = QLabel('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('font-size:12px;')
        self.error_label.hide()
        bottom_layout.addWidget(self.error_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.later_btn = QPushButton('稍后更新')
        self.later_btn.clicked.connect(self.reject)
        self.download_btn = QPushButton('下载更新')
        self.download_btn.clicked.connect(self._on_primary_clicked)
        row.addWidget(self.later_btn)
        row.addWidget(self.download_btn)
        bottom_layout.addLayout(row)
        outer.addWidget(bottom)
        # 本对话框自己处理明暗主题，避免 ThemeManager 再对已适配的
        # 浅色样式做二次反转。
        for widget in [self, *self.findChildren(QWidget)]:
            widget.setProperty('_theme_ignore', True)
        self._apply_theme()
        cached = self._find_cached_asset()
        if cached:
            self._set_ready(cached)

    def _apply_primary_button(self):
        state = self._primary_state
        light = bool(self._light)
        if state in ('download', 'retry'):
            background, foreground = '#90D9D9', '#122828'
        elif state == 'ready':
            background, foreground = '#2e7d32', '#ffffff'
        else:
            background = '#edf1f1' if light else '#30333b'
            foreground = '#8a9494' if light else '#858a94'
        labels = {
            'download': '下载更新',
            'retry': '重试下载',
            'downloading': '下载中',
            'ready': '重启安装',
            'restarting': '正在重启...',
        }
        self.download_btn.setText(labels.get(state, '下载更新'))
        self.download_btn.setEnabled(state not in ('downloading', 'restarting'))
        self.download_btn.setStyleSheet(
            f'QPushButton {{ background:{background}; color:{foreground};'
            ' border:none; border-radius:7px; padding:7px 18px; }'
        )

    def _apply_theme(self):
        light = bool(self._light)
        background = '#f7fbfb' if light else '#1f2229'
        text = '#1b1b1b' if light else '#f0f0f0'
        muted = 'rgba(0,0,0,0.58)' if light else 'rgba(255,255,255,0.72)'
        body_color = 'rgba(0,0,0,0.66)' if light else 'rgba(255,255,255,0.58)'
        status_color = 'rgba(0,0,0,0.58)' if light else 'rgba(255,255,255,0.62)'
        error_color = '#B42318' if light else '#ff8a8a'
        button_bg = '#e8efef' if light else '#3a3d47'
        button_hover = '#dbe7e7' if light else '#4a4d57'
        disabled_bg = '#edf1f1' if light else '#30333b'
        disabled_text = '#8a9494' if light else '#858a94'
        track = 'rgba(0,0,0,0.10)' if light else 'rgba(255,255,255,0.12)'
        chunk = '#409999' if light else '#88DDDD'
        self.setStyleSheet(
            f'QDialog {{ background:{background}; }}'
            f'QLabel {{ color:{text}; background:transparent; }}'
            f'QProgressBar {{ background:{track}; border:none; border-radius:6px; height:12px; }}'
            f'QProgressBar::chunk {{ background:{chunk}; border-radius:6px; }}'
            f'QPushButton {{ background:{button_bg}; color:{text}; border:1px solid '
            + ('rgba(0,0,0,0.12)' if light else 'rgba(255,255,255,0.10)')
            + '; border-radius:7px; padding:7px 18px; }'
            f'QPushButton:hover {{ background:{button_hover}; }}'
            f'QPushButton:disabled {{ background:{disabled_bg}; color:{disabled_text}; }}'
        )
        self.name_label.setStyleSheet(f'color:{muted}; font-size:13px;')
        self.status_label.setStyleSheet(f'color:{status_color}; font-size:12px;')
        self.error_label.setStyleSheet(f'color:{error_color}; font-size:12px;')
        if self.body is not None:
            self.body.setStyleSheet(
                'QTextBrowser { background:transparent; border:none;'
                f' color:{body_color}; font-size:12px; }}'
            )
        self.progress.set_fill_color(chunk)
        self._apply_primary_button()

    def set_light(self, light):
        light = bool(light)
        if light == getattr(self, '_light', None):
            return
        self._light = light
        self._apply_theme()

    def showEvent(self, event):
        try:
            light = (
                QApplication.palette().color(QPalette.Window).lightness() > 128
            )
            if light != self._light:
                self._light = light
                self._apply_theme()
        except Exception:
            pass
        super().showEvent(event)

    def begin_restart(self):
        # 自动下载完成后立刻锁定重启按钮，避免用户重复触发更新。
        self._primary_state = 'restarting'
        self.later_btn.setEnabled(False)
        self.status_label.setText('更新已下载，正在重启安装...')
        self._apply_primary_button()

    def _find_cached_asset(self):
        name = str(self.info.get('asset_name') or '').strip()
        if not name:
            return ''
        filename = re.sub(r'[^A-Za-z0-9._-]', '_', name)
        path = scl_dir() / 'update' / 'downloads' / filename
        if not path.is_file():
            return ''
        expected = int(self.info.get('asset_size') or 0)
        if expected and path.stat().st_size != expected:
            return ''
        return str(path)

    def _set_ready(self, path):
        self.asset_path = str(path)
        self.progress.show()
        self.progress.setValue(1000)
        self.error_label.hide()
        self.status_label.setText('更新已下载，点击“重启安装”完成更新。也可以关闭窗口稍后更新。')
        self._primary_state = 'ready'
        self.later_btn.setText('稍后更新')
        self.later_btn.setEnabled(True)
        self._apply_primary_button()

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
        self._primary_state = 'downloading'
        self.later_btn.setText('取消')
        self.later_btn.setEnabled(True)
        self._apply_primary_button()
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
        self._set_ready(path)
        version = str(self.info.get('version') or self.info.get('tag') or '')
        self.download_completed.emit(str(path), version)

    def _on_failed(self, message):
        self.error = str(message)
        self.error_label.setText(str(message))
        self.error_label.show()
        self.progress.hide()
        self._primary_state = 'retry'
        self.later_btn.setText('关闭')
        self.later_btn.setEnabled(True)
        self._apply_primary_button()

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
        # 关闭弹窗代表稍后更新，不在这里替换客户端。
        self._cancel_worker()
        super().reject()

    def closeEvent(self, event):
        self._cancel_worker()
        super().closeEvent(event)


__all__ = ['UpdateDialog']
