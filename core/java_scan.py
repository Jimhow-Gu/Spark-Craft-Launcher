'''启动时后台检测 Java，避免设置页和主窗口初始化卡顿。'''

from __future__ import annotations
from PySide6.QtCore import (QObject, QThread, Signal)
from core.launch import LaunchManager

class _JavaScanWorker(QThread):
    result_ready = Signal(list)
    def run(self):
        try:
            result = LaunchManager().refresh_java_list()
        except Exception:
            result = LaunchManager().peek_cached_java_list() or []
        self.result_ready.emit(list(result or []))


class JavaScanService(QObject):
    finished = Signal(list)
    _instance = None
    def __init__(self):
        super().__init__()
        self._worker = None
        self._done = False
        self._result = []

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = JavaScanService()
        return cls._instance

    def is_running(self):
        return self._worker is not None and self._worker.isRunning()

    def start(self, force=False):
        if self.is_running():
            return
        if self._done and not force:
            return
        self._done = False
        worker = _JavaScanWorker()
        self._worker = worker
        worker.result_ready.connect(self._on_result)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_result(self, result):
        self._result = list(result or [])
        self._done = True
        self._worker = None
        self.finished.emit(list(self._result))

    def peek(self):
        if self._done:
            return list(self._result)
        return LaunchManager().peek_cached_java_list()

    def shutdown(self):
        worker = self._worker
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.wait(3000)
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(500)
        except RuntimeError:
            pass
        self._worker = None


