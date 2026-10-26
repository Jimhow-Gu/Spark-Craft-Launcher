'''下载管理页面：显示所有下载/修复任务与步骤。'''

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)
from core.download_manager import DownloadManager
from ui.widgets.back_button import BackButton
from ui.widgets.smooth_progress import SmoothProgressBar


class _TaskCard(QFrame):
    remove_requested = Signal(str)

    def __init__(self, task: dict, parent=None, start_percent: float = 0.0):
        super().__init__(parent)
        self.task_id = task.get('id', '')
        self.setObjectName('DownloadTaskCard')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            '\n            QFrame#DownloadTaskCard { background:rgba(30,32,40,0.9);\n'
            "                border:1px solid rgba(255,255,255,0.22); border-radius:10px; }\n"
            '            QLabel { background:transparent; }\n        '
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.title_label = QLabel(task.get('title', '下载任务'))
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(0)
        self.title_label.setStyleSheet('color:#f0f0f0; font-size:14px; font-weight:600;')
        header.addWidget(self.title_label, 1)
        header.addStretch(1)
        self.pct_label = QLabel(f'{float(task.get("percent", 0)):.2f}%')
        self.pct_label.setStyleSheet('color:#9ccaff; font-size:13px; font-weight:600;')
        header.addWidget(self.pct_label)
        self.close = QPushButton('✕')
        self.close.setFixedSize(24, 24)
        self.close.setToolTip('关闭任务' if task.get('done') else '取消并关闭下载')
        self.close.setStyleSheet(
            'QPushButton { background:transparent; color:#aaa; border:none; font-size:13px; }'
            'QPushButton:hover { color:#fff; background:rgba(255,255,255,0.12); border-radius:12px; }'
        )
        self.close.clicked.connect(lambda: self.remove_requested.emit(self.task_id))
        header.addWidget(self.close)
        layout.addLayout(header)
        self.bar = SmoothProgressBar()
        self.bar.setRange(0, 1000)
        start = int(max(0.0, min(100.0, float(start_percent))) * 10)
        self.bar.setValue(start)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(7)
        self.bar.setStyleSheet(
            '\n            QProgressBar { background:rgba(255,255,255,0.12); border:none; border-radius:3px; }\n'
            '            QProgressBar::chunk { background:#0078d4; border-radius:3px; }\n        '
        )
        self.bar.valueChanged.connect(self._on_bar_value)
        layout.addWidget(self.bar)
        percent = float(task.get('percent', 0))
        if int(percent * 10) > start:
            self.bar.set_animated_value(int(max(0.0, min(100, percent)) * 10))
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)
        self.steps_layout = QVBoxLayout()
        self.steps_layout.setContentsMargins(0, 0, 0, 0)
        self.steps_layout.setSpacing(0)
        layout.addLayout(self.steps_layout)
        self._step_rows = []
        self.update_task(task)

    def _on_bar_value(self, value):
        try:
            self.pct_label.setText(f'{value / 10:.2f}%')
        except RuntimeError:
            pass

    def update_task(self, task: dict):
        '''原地更新卡片内容，避免整卡重建造成的闪烁。'''

        title = str(task.get('title', '下载任务'))
        if self.title_label.text() != title:
            self.title_label.setText(title)
        percent = max(0.0, min(100.0, float(task.get('percent', 0))))
        self.bar.set_animated_value(int(percent * 10))
        self.close.setToolTip('关闭任务' if task.get('done') else '取消并关闭下载')
        status = task.get('status') or ''
        if status:
            failed = task.get('done') and task.get('success') is False
            self.status_label.setText(status)
            self.status_label.setStyleSheet(
                'color:#ff8a8a; font-size:12px;'
                if failed else
                'color:rgba(255,255,255,0.58); font-size:12px;'
            )
            self.status_label.show()
        else:
            self.status_label.clear()
            self.status_label.hide()
        self._sync_steps(task.get('steps', []))

    def _sync_steps(self, steps):
        '''步骤行就地刷新：仅行数变化时重建，避免进度跳动时反复重建闪烁。'''

        if len(self._step_rows) != len(steps):
            while self.steps_layout.count():
                item = self.steps_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self._step_rows = []
            for _ in steps:
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(8)
                icon = QLabel()
                icon.setFixedWidth(18)
                icon.setStyleSheet('font-size:15px; font-weight:700;')
                text = QLabel()
                text.setWordWrap(True)
                text.setStyleSheet('font-size:13px;')
                lay.addWidget(icon)
                lay.addWidget(text, 1)
                self.steps_layout.addWidget(row)
                self._step_rows.append((icon, text))
        for (icon, text), step in zip(self._step_rows, steps):
            state = step.get('state', 'pending')
            mark = {'done': '✓', 'active': '●', 'error': '✕'}.get(state, '•')
            color = {'done': '#4da3ff', 'active': '#4da3ff',
                     'error': '#ff6b6b'}.get(state, 'rgba(255,255,255,0.35)')
            icon.setText(mark)
            icon.setStyleSheet(f'color:{color}; font-size:15px; font-weight:700;')
            text.setText(f'{step.get("text", "")}    {float(step.get("percent", 0)):.0f}%')
            text.setStyleSheet(
                f'color:{"rgba(255,255,255,0.8)" if state in ("done", "active") else "rgba(255,255,255,0.5)"};'
                'font-size:13px;'
            )


class DownloadManagerPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = DownloadManager.instance()
        self._percent_cache = {}
        self._had_tasks = False
        self._cards = {}
        self.setObjectName('DownloadManagerPage')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet('QWidget#DownloadManagerPage { background:transparent; }')
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 18, 26, 18)
        root.setSpacing(12)
        top = QHBoxLayout()
        back = BackButton()
        back.clicked.connect(lambda: self.back_requested.emit())
        title = QLabel('下载管理')
        title.setStyleSheet('color:#f0f0f0; font-size:22px; font-weight:600;')
        top.addWidget(back)
        top.addWidget(title)
        top.addStretch(1)
        root.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(18)
        stats = QFrame()
        stats.setObjectName('DownloadStats')
        stats.setAttribute(Qt.WA_StyledBackground, True)
        stats.setFixedWidth(190)
        stats.setStyleSheet(
            'QFrame#DownloadStats { background:rgba(30,32,40,0.9);'
            ' border:1px solid rgba(255,255,255,0.22); border-radius:10px; }'
            ' QLabel { background:transparent; }'
        )
        stats_layout = QVBoxLayout(stats)
        stats_layout.setContentsMargins(16, 22, 16, 22)
        stats_layout.setSpacing(6)
        total_title = QLabel('总进度')
        total_title.setAlignment(Qt.AlignCenter)
        total_title.setStyleSheet('color:#9ccaff; font-size:14px;')
        stats_layout.addWidget(total_title)
        self.total_label = QLabel('0.00 %')
        self.total_label.setAlignment(Qt.AlignCenter)
        self.total_label.setStyleSheet('color:#f0f0f0; font-size:24px; font-weight:600;')
        stats_layout.addWidget(self.total_label)
        stats_layout.addSpacing(16)
        speed_title = QLabel('下载速度')
        speed_title.setAlignment(Qt.AlignCenter)
        speed_title.setStyleSheet('color:#9ccaff; font-size:14px;')
        stats_layout.addWidget(speed_title)
        self.speed_label = QLabel('0 B/s')
        self.speed_label.setAlignment(Qt.AlignCenter)
        self.speed_label.setStyleSheet('color:#f0f0f0; font-size:20px;')
        stats_layout.addWidget(self.speed_label)
        stats_layout.addSpacing(16)
        remaining_title = QLabel('剩余文件')
        remaining_title.setAlignment(Qt.AlignCenter)
        remaining_title.setStyleSheet('color:#9ccaff; font-size:14px;')
        stats_layout.addWidget(remaining_title)
        self.remaining_label = QLabel('0')
        self.remaining_label.setAlignment(Qt.AlignCenter)
        self.remaining_label.setStyleSheet('color:#f0f0f0; font-size:20px;')
        stats_layout.addWidget(self.remaining_label)
        stats_layout.addStretch(1)
        body.addWidget(stats)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(
            'QScrollArea { background:transparent; border:none; }'
            ' QScrollArea > QWidget > QWidget { background:transparent; }'
        )
        self.scroll.viewport().setStyleSheet('background:transparent;')
        self.tasks_widget = QWidget()
        self.tasks_widget.setMinimumWidth(0)
        self.tasks_widget.setStyleSheet('background:transparent;')
        self.tasks_layout = QVBoxLayout(self.tasks_widget)
        self.tasks_layout.setContentsMargins(0, 0, 0, 0)
        self.tasks_layout.setSpacing(10)
        self.tasks_layout.addStretch(1)
        self.scroll.setWidget(self.tasks_widget)
        body.addWidget(self.scroll, 1)
        root.addLayout(body, 1)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self.refresh)
        self.manager.changed.connect(self._on_manager_changed)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._had_tasks = bool(self.manager.tasks())

    def _on_manager_changed(self):
        '''任务完成后若无剩余任务，自动回到打开下载管理前的页面。'''

        tasks = self.manager.tasks()
        if tasks:
            self._had_tasks = True
        elif self._had_tasks:
            self._had_tasks = False
            if self.isVisible():
                QTimer.singleShot(0, self.back_requested.emit)
        self._schedule_refresh()

    def _schedule_refresh(self):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _close_task(self, task_id):
        '''关闭任务；最后一个任务被关闭后由 changed 信号触发自动返回。'''

        self.manager.cancel_task(task_id)

    def _update_stats(self, tasks):
        active = [t for t in tasks if not t.get('done')]
        if active:
            snapshot = self.manager.snapshot()
            self.total_label.setText(f'{float(snapshot.get("percent", 0)):.2f} %')
            self.speed_label.setText(snapshot.get('speed') or '0 B/s')
            remaining = str(snapshot.get('remaining', '') or '')
            if remaining and remaining != '0':
                self.remaining_label.setText(remaining)
            else:
                pending_steps = sum(
                    (1 for task in active for step in task.get('steps', [])
                     if step.get('state') in ('pending', 'active'))
                )
                self.remaining_label.setText(str(pending_steps))
            return
        failed = [t for t in tasks if t.get('done') and not t.get('success')]
        if failed:
            self.total_label.setText(f'{float(failed[-1].get("percent", 0)):.2f} %')
        else:
            self.total_label.setText('0.00 %')
        self.speed_label.setText('0 B/s')
        self.remaining_label.setText('0')

    def refresh(self):
        tasks = self.manager.tasks()
        self._update_stats(tasks)
        seen = set()
        for task in reversed(tasks):
            task_id = str(task.get('id', ''))
            seen.add(task_id)
            card = self._cards.get(task_id)
            if card is None:
                start_percent = self._percent_cache.get(task_id, 0.0)
                card = _TaskCard(task, start_percent=start_percent)
                card.remove_requested.connect(self._close_task)
                self._cards[task_id] = card
                # 插到末尾的 stretch 之前
                self.tasks_layout.insertWidget(
                    max(0, self.tasks_layout.count() - 1), card
                )
            else:
                try:
                    card.update_task(task)
                except RuntimeError:
                    self._cards.pop(task_id, None)
        for task_id, card in list(self._cards.items()):
            if task_id not in seen:
                self._cards.pop(task_id, None)
                try:
                    card.deleteLater()
                except RuntimeError:
                    pass
        self._percent_cache = {
            str(task.get('id', '')): float(task.get('percent', 0))
            for task in tasks
        }
