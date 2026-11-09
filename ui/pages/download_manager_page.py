'''下载管理页面：显示所有下载/修复任务与步骤。'''

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)
from core.download_manager import DownloadManager
from ui.design import (ACCENT_TEXT, DEEP_ACCENT_LIGHT, MUTED_LIGHT,
                       TEXT_DARK, TEXT_LIGHT)
from ui.widgets.back_button import BackButton
from ui.widgets.smooth_progress import SmoothProgressBar


def _is_light_theme():
    try:
        palette = QApplication.palette()
        return palette.color(QPalette.Window).lightness() > 128
    except Exception:
        return False


class _TaskCard(QFrame):
    remove_requested = Signal(str)

    def __init__(self, task: dict, parent=None, start_percent: float = 0.0):
        super().__init__(parent)
        self.task_id = task.get('id', '')
        self._light = _is_light_theme()
        self._task = dict(task)
        self.setObjectName('DownloadTaskCard')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty('_theme_ignore', True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.title_label = QLabel(task.get('title', '下载任务'))
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(0)
        self.title_label.setProperty('_theme_ignore', True)
        header.addWidget(self.title_label, 1)
        header.addStretch(1)
        self.pct_label = QLabel(f'{float(task.get("percent", 0)):.2f}%')
        self.pct_label.setProperty('_theme_ignore', True)
        header.addWidget(self.pct_label)
        self.close = QPushButton('✕')
        self.close.setFixedSize(24, 24)
        self.close.setToolTip('关闭任务' if task.get('done') else '取消并关闭下载')
        self.close.setProperty('_theme_ignore', True)
        self.close.clicked.connect(lambda: self.remove_requested.emit(self.task_id))
        header.addWidget(self.close)
        layout.addLayout(header)
        self.bar = SmoothProgressBar()
        self.bar.setRange(0, 1000)
        start = int(max(0.0, min(100.0, float(start_percent))) * 10)
        self.bar.setValue(start)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(7)
        self.bar.setProperty('_theme_ignore', True)
        self.bar.valueChanged.connect(self._on_bar_value)
        layout.addWidget(self.bar)
        percent = float(task.get('percent', 0))
        if int(percent * 10) > start:
            self.bar.set_animated_value(int(max(0.0, min(100, percent)) * 10))
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setProperty('_theme_ignore', True)
        self.status_label.hide()
        layout.addWidget(self.status_label)
        self.steps_layout = QVBoxLayout()
        self.steps_layout.setContentsMargins(0, 0, 0, 0)
        self.steps_layout.setSpacing(0)
        layout.addLayout(self.steps_layout)
        self._step_rows = []
        self._apply_theme()
        self.update_task(task)

    def _apply_theme(self):
        light = self._light
        background = 'rgba(249,251,251,0.94)' if light else 'rgba(30,32,40,0.9)'
        border = 'rgba(64,153,153,0.28)' if light else 'rgba(255,255,255,0.22)'
        self.setStyleSheet(
            f'QFrame#DownloadTaskCard {{ background:{background};'
            f' border:1px solid {border}; border-radius:10px; }}'
            'QFrame#DownloadTaskCard QLabel { background:transparent; }'
        )
        self.title_label.setStyleSheet(
            f'color:{TEXT_LIGHT if light else TEXT_DARK};'
            'font-size:14px; font-weight:900;'
        )
        self.pct_label.setStyleSheet(
            f'color:{DEEP_ACCENT_LIGHT if light else ACCENT_TEXT};'
            'font-size:13px; font-weight:900;'
        )
        if light:
            close_style = (
                'QPushButton { background:transparent; color:#666; border:none; font-size:13px; }'
                'QPushButton:hover { color:#111; background:rgba(0,0,0,0.10); border-radius:12px; }'
            )
            bar_style = (
                'QProgressBar { background:rgba(64,153,153,0.16); border:none; border-radius:3px; }'
                f'QProgressBar::chunk {{ background:{DEEP_ACCENT_LIGHT}; border-radius:3px; }}'
            )
        else:
            close_style = (
                'QPushButton { background:transparent; color:#aaa; border:none; font-size:13px; }'
                'QPushButton:hover { color:#fff; background:rgba(255,255,255,0.12); border-radius:12px; }'
            )
            bar_style = (
                'QProgressBar { background:rgba(255,255,255,0.12); border:none; border-radius:3px; }'
                'QProgressBar::chunk { background:#88DDDD; border-radius:3px; }'
            )
        self.close.setStyleSheet(close_style)
        self.bar.setStyleSheet(bar_style)

    def set_light(self, light):
        light = bool(light)
        if light == self._light:
            return
        self._light = light
        self._apply_theme()
        self.update_task(self._task)

    def _on_bar_value(self, value):
        try:
            self.pct_label.setText(f'{value / 10:.2f}%')
        except RuntimeError:
            pass

    def update_task(self, task: dict):
        '''原地更新卡片内容，避免整卡重建造成的闪烁。'''

        self._task = dict(task)
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
            if failed:
                color = '#B42318' if self._light else '#ff8a8a'
            else:
                color = MUTED_LIGHT if self._light else 'rgba(255,255,255,0.58)'
            self.status_label.setStyleSheet(f'color:{color}; font-size:12px;')
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
                icon.setStyleSheet('font-size:15px; font-weight:900;')
                text = QLabel()
                text.setWordWrap(True)
                text.setStyleSheet('font-size:13px;')
                icon.setProperty('_theme_ignore', True)
                text.setProperty('_theme_ignore', True)
                lay.addWidget(icon)
                lay.addWidget(text, 1)
                self.steps_layout.addWidget(row)
                self._step_rows.append((icon, text))
        light = self._light
        active_color = DEEP_ACCENT_LIGHT if light else ACCENT_TEXT
        pending_icon = 'rgba(0,0,0,0.35)' if light else 'rgba(255,255,255,0.35)'
        active_text = 'rgba(0,0,0,0.76)' if light else 'rgba(255,255,255,0.8)'
        pending_text = 'rgba(0,0,0,0.48)' if light else 'rgba(255,255,255,0.5)'
        for (icon, text), step in zip(self._step_rows, steps):
            state = step.get('state', 'pending')
            mark = {'done': '✓', 'active': '●', 'error': '✕'}.get(state, '•')
            color = {'done': active_color, 'active': active_color,
                     'error': '#B42318' if light else '#ff6b6b'}.get(
                         state, pending_icon
                     )
            icon.setText(mark)
            icon.setStyleSheet(f'color:{color}; font-size:15px; font-weight:900;')
            text.setText(f'{step.get("text", "")}    {float(step.get("percent", 0)):.0f}%')
            text.setStyleSheet(
                f'color:{active_text if state in ("done", "active") else pending_text};'
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
        self._light = _is_light_theme()
        self.setObjectName('DownloadManagerPage')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet('QWidget#DownloadManagerPage { background:transparent; }')
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 18, 26, 18)
        root.setSpacing(12)
        top = QHBoxLayout()
        back = BackButton()
        back.clicked.connect(lambda: self.back_requested.emit())
        self.title_label = QLabel('下载管理')
        self.title_label.setProperty('_theme_ignore', True)
        top.addWidget(back)
        top.addWidget(self.title_label)
        top.addStretch(1)
        root.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(18)
        self.stats = QFrame()
        self.stats.setObjectName('DownloadStats')
        self.stats.setAttribute(Qt.WA_StyledBackground, True)
        self.stats.setFixedWidth(190)
        self.stats.setProperty('_theme_ignore', True)
        stats_layout = QVBoxLayout(self.stats)
        stats_layout.setContentsMargins(16, 22, 16, 22)
        stats_layout.setSpacing(6)
        self.total_title = QLabel('总进度')
        self.total_title.setAlignment(Qt.AlignCenter)
        self.total_title.setProperty('_theme_ignore', True)
        stats_layout.addWidget(self.total_title)
        self.total_label = QLabel('0.00 %')
        self.total_label.setAlignment(Qt.AlignCenter)
        self.total_label.setProperty('_theme_ignore', True)
        stats_layout.addWidget(self.total_label)
        stats_layout.addSpacing(16)
        self.speed_title = QLabel('下载速度')
        self.speed_title.setAlignment(Qt.AlignCenter)
        self.speed_title.setProperty('_theme_ignore', True)
        stats_layout.addWidget(self.speed_title)
        self.speed_label = QLabel('0 B/s')
        self.speed_label.setAlignment(Qt.AlignCenter)
        self.speed_label.setProperty('_theme_ignore', True)
        stats_layout.addWidget(self.speed_label)
        stats_layout.addSpacing(16)
        self.remaining_title = QLabel('剩余文件')
        self.remaining_title.setAlignment(Qt.AlignCenter)
        self.remaining_title.setProperty('_theme_ignore', True)
        stats_layout.addWidget(self.remaining_title)
        self.remaining_label = QLabel('0')
        self.remaining_label.setAlignment(Qt.AlignCenter)
        self.remaining_label.setProperty('_theme_ignore', True)
        stats_layout.addWidget(self.remaining_label)
        stats_layout.addStretch(1)
        body.addWidget(self.stats)
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
        self._apply_theme()
        self.refresh()

    def _apply_theme(self):
        light = self._light
        self.title_label.setStyleSheet(
            f'color:{TEXT_LIGHT if light else TEXT_DARK};'
            'font-size:22px; font-weight:900;'
        )
        background = 'rgba(249,251,251,0.94)' if light else 'rgba(30,32,40,0.9)'
        border = 'rgba(64,153,153,0.28)' if light else 'rgba(255,255,255,0.22)'
        self.stats.setStyleSheet(
            f'QFrame#DownloadStats {{ background:{background};'
            f' border:1px solid {border}; border-radius:10px; }}'
            'QFrame#DownloadStats QLabel { background:transparent; }'
        )
        accent = DEEP_ACCENT_LIGHT if light else ACCENT_TEXT
        primary = TEXT_LIGHT if light else TEXT_DARK
        for label in (self.total_title, self.speed_title, self.remaining_title):
            label.setStyleSheet(f'color:{accent}; font-size:14px;')
        self.total_label.setStyleSheet(
            f'color:{primary}; font-size:24px; font-weight:900;'
        )
        self.speed_label.setStyleSheet(f'color:{primary}; font-size:20px;')
        self.remaining_label.setStyleSheet(f'color:{primary}; font-size:20px;')

    def set_light(self, light):
        light = bool(light)
        if light == self._light:
            return
        self._light = light
        self._apply_theme()
        for card in list(self._cards.values()):
            try:
                card.set_light(light)
            except RuntimeError:
                continue

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
        snapshot = self.manager.snapshot()
        if snapshot.get('failed'):
            self.total_label.setText(
                f'{float(snapshot.get("percent", 0)):.2f} %'
            )
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
