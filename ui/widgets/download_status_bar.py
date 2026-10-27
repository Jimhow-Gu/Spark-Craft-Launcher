'''所有页面底部可见的下载进度条。'''

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, Qt, Signal)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel)
from ui.widgets.smooth_progress import RoundedProgressBar

class DownloadStatusBar(QFrame):
    clicked = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._height_anim = None
        self._progress_anim = None
        self._target_height = 34
        self._hiding = False
        self.setStyleSheet('\n            QFrame { background:rgba(20,22,28,0.92); border-top:1px solid rgba(255,255,255,0.12);\n                     border-bottom-right-radius:14px; }\n            QLabel { color:rgba(255,255,255,0.82); font-size:12px; background:transparent; }\n            QProgressBar { background:rgba(255,255,255,0.12); border:none;\n                           border-radius:4px; height:8px; text-align:center; }\n            QProgressBar::chunk { background:#0078d4; border-radius:4px; }\n        ')
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
        anim = QPropertyAnimation(self, b'maximumHeight', self)
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
        anim = QPropertyAnimation(self, b'maximumHeight', self)
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
        if self._progress_anim is not None:
            self._progress_anim.stop()
        anim = QPropertyAnimation(self.progress, b'value', self)
        anim.setDuration(220)
        anim.setStartValue(self.progress.value())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        self._progress_anim = anim
        anim.start()

    def set_snapshot(self, snapshot: dict):
        if snapshot:
            if not (snapshot.get('active') or snapshot.get('failed')):
                self._animate_hide()
                return
        failed = bool(snapshot.get('failed')) and not snapshot.get('active')
        percent = max(0.0, min(100.0, float(snapshot.get('percent', 0))))
        self.title_label.setText(snapshot.get('title', '下载任务'))
        self.title_label.setToolTip('\n'.join((part for part in (snapshot.get('title', ''), snapshot.get('status', '')) if part)))
        self._animate_progress(int(percent * 10))
        self.percent_label.setText(f'{percent:.2f}%')
        self.percent_label.setStyleSheet('color:#ff8a8a; font-size:12px; background:transparent;' if failed else 'color:rgba(255,255,255,0.82); font-size:12px; background:transparent;')
        self.progress.setStyleSheet('QProgressBar { background:rgba(255,255,255,0.12); border:none; border-radius:4px; height:8px; } QProgressBar::chunk { background:#d83b3b; border-radius:4px; }' if failed else 'QProgressBar { background:rgba(255,255,255,0.12); border:none; border-radius:4px; height:8px; } QProgressBar::chunk { background:#0078d4; border-radius:4px; }')
        speed = snapshot.get('speed') or '0 B/s'
        self.speed_label.setText('失败' if failed else speed)
        self._animate_show()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


