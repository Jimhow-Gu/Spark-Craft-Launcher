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
    '''取消并有限等待仍运行的 QThread；不调用 terminate()。

    QThread.terminate() 在打包版退出时可能与 Qt 媒体/网络线程互相等待，
    且调用期间持有 GIL，连 os._exit 兜底都无法执行。未及时退出的线程
    随进程结束由系统回收，比卡住整个退出流程安全。
    '''

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
            except (RuntimeError, ReferenceError):
                continue
        gc.collect()


_INSTALLED = False


def install_thread_guard(app):
    global _INSTALLED
    if app is None or _INSTALLED:
        return
    _INSTALLED = True
    app.aboutToQuit.connect(shutdown_threads)
    atexit.register(shutdown_threads)


__all__ = ['install_thread_guard', 'shutdown_threads']
