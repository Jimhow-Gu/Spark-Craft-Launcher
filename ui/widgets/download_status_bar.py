'''所有页面底部可见的下载进度条。'''

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, Qt, Signal)
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel)
from ui.widgets.height_animation import HeightAnimation
from ui.widgets.smooth_progress import RoundedProgressBar

class DownloadStatusBar(QFrame):
    clicked = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._height_anim = None
        self._progress_anim = None
        self._progress_target = -1
        self._failed_view = None
        self._target_height = 34
        self._hiding = False
        try:
            self._light = (
                QApplication.palette().color(QPalette.Window).lightness() > 128
            )
        except Exception:
            self._light = False
        self.setProperty('_theme_ignore', True)
        self._apply_theme()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(12)
        self.title_label = QLabel('')
        self.title_label.setMinimumWidth(150)
        layout.addWidget(self.title_label)
        self.progress = RoundedProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        layout.addWidget(self.progress, 1)
        self._progress_anim = QPropertyAnimation(self.progress, b'value', self)
        self._progress_anim.setDuration(160)
        self._progress_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.percent_label = QLabel('0%')
        self.percent_label.setFixedWidth(56)
        self.percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.percent_label)
        self.speed_label = QLabel('')
        self.speed_label.setFixedWidth(80)
        self.speed_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.speed_label)
        self.setMaximumHeight(0)
        self.hide()
        self._apply_theme()

    def _apply_theme(self):
        light = bool(getattr(self, '_light', False))
        failed = bool(self._failed_view)
        background = 'rgba(248,251,251,0.96)' if light else 'rgba(18,23,23,0.94)'
        border = 'rgba(64,153,153,0.22)' if light else 'rgba(255,255,255,0.12)'
        text = 'rgba(31,52,52,0.86)' if light else 'rgba(255,255,255,0.82)'
        track = 'rgba(64,153,153,0.16)' if light else 'rgba(255,255,255,0.12)'
        fill = (
            '#B42318' if failed and light else
            '#d83b3b' if failed else
            '#409999' if light else '#88DDDD'
        )
        self.setStyleSheet(
            f'QFrame {{ background:{background};'
            f' border-top:1px solid {border};'
            ' border-bottom-right-radius:14px; }'
            f'QLabel {{ color:{text}; font-size:12px; background:transparent; }}'
            f'QProgressBar {{ background:{track}; border:none;'
            f' border-radius:4px; height:8px; text-align:center; }}'
            f'QProgressBar::chunk {{ background:{fill}; border-radius:4px; }}'
        )
        for label in (
            getattr(self, 'title_label', None),
            getattr(self, 'speed_label', None),
        ):
            if label is not None:
                label.setProperty('_theme_ignore', True)
                label.setStyleSheet(
                    f'color:{text}; font-size:12px; background:transparent;'
                )
        percent = getattr(self, 'percent_label', None)
        if percent is not None:
            percent.setProperty('_theme_ignore', True)
            percent.setStyleSheet(
                f'color:{"#B42318" if light else "#ff8a8a"};'
                'font-size:12px; background:transparent;'
                if failed else
                f'color:{text}; font-size:12px; background:transparent;'
            )
        progress = getattr(self, 'progress', None)
        if progress is not None:
            progress.setProperty('_theme_ignore', True)
            progress.set_fill_color(fill)

    def set_light(self, light):
        light = bool(light)
        if light == getattr(self, '_light', None):
            return
        self._light = light
        self._apply_theme()

    def _animate_show(self):
        if self._hiding:
            self._hiding = False
            if self._height_anim is not None:
                self._height_anim.stop()
        target = max(30, self.layout().sizeHint().height())
        self._target_height = target
        if not self.isVisible():
            self.setMaximumHeight(0)
            self.show()
        if self.maximumHeight() >= target:
            return
        start = self.maximumHeight() if self.isVisible() else 0
        anim = HeightAnimation(self, self)
        anim.setDuration(200)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect((lambda: self.setMaximumHeight(target)))
        self._height_anim = anim
        anim.start()

    def _animate_hide(self):
        if not self.isVisible():
            return
        self._hiding = True
        start = self.height()
        anim = HeightAnimation(self, self)
        anim.setDuration(170)
        anim.setStartValue(start)
        anim.setEndValue(0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        def finish():
            if self._hiding:
                self.hide()
                self.setMaximumHeight(0)
                self._hiding = False

        anim.finished.connect(finish)
        self._height_anim = anim
        anim.start()

    def _animate_progress(self, value: int):
        target = max(0, min(1000, int(value)))
        if target == self._progress_target:
            return
        self._progress_target = target
        anim = self._progress_anim
        if anim is None:
            self.progress.setValue(target)
            return
        anim.stop()
        anim.setStartValue(self.progress.value())
        anim.setEndValue(target)
        anim.start()

    def set_snapshot(self, snapshot: dict):
        if snapshot:
            if not (snapshot.get('active') or snapshot.get('failed')):
                self._animate_hide()
                return
        failed = bool(snapshot.get('failed')) and not snapshot.get('active')
        percent = max(0.0, min(100.0, float(snapshot.get('percent', 0))))
        title = snapshot.get('title', '下载任务')
        if self.title_label.text() != title:
            self.title_label.setText(title)
        self.title_label.setToolTip('\n'.join((part for part in (title, snapshot.get('status', '')) if part)))
        self._animate_progress(int(percent * 10))
        self.percent_label.setText(f'{percent:.2f}%')
        if self._failed_view is not failed:
            self._failed_view = failed
            self._apply_theme()
        speed = snapshot.get('speed') or '0 B/s'
        self.speed_label.setText('失败' if failed else speed)
        self._animate_show()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


