'''全局 QThread 兜底回收：页面切走后残留的加载线程在退出时统一收尾。'''

from __future__ import annotations

import atexit
import gc
import logging

from PySide6.QtCore import QThread

logger = logging.getLogger(__name__)


def _running_threads():
    result = []
    for obj in gc.get_objects():
        try:
            if isinstance(obj, QThread) and obj.isRunning():
                result.append(obj)
        except (RuntimeError, ReferenceError):
            continue
    return result


def shutdown_threads(timeout_ms=700):
    '''取消并等待所有仍运行的 QThread，最后才对无响应线程 terminate。'''

    for _ in range(2):
        threads = _running_threads()
        if not threads:
            break
        for worker in threads:
            cancel = getattr(worker, 'cancel', None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass
        for worker in threads:
            try:
                if worker.isRunning():
                    worker.wait(timeout_ms)
                    if worker.isRunning():
                        worker.terminate()
                        worker.wait(200)
            except (RuntimeError, ReferenceError):
                continue
        gc.collect()


def install_thread_guard(app):
    if app is None:
        return
    app.aboutToQuit.connect(shutdown_threads)
    atexit.register(shutdown_threads)


__all__ = ['install_thread_guard', 'shutdown_threads']
