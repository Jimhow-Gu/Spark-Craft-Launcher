'''全局异常兜底：未捕获异常只记录日志并提示，不让异常逃出 UI 事件循环。'''

from __future__ import annotations

import gc
import logging
import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

logger = logging.getLogger('ErrorGuard')

_app = None
_installed = False
_notice_pending = False
_last_notice_at = 0.0
_notifier = None
_previous_excepthook = None
_previous_thread_hook = None


class _NoticeRelay(QObject):
    '''把后台线程的异常提示安全地转回主线程。'''

    notice = Signal(str)


def install_error_guard(app=None):
    '''安装主线程、Python 线程和析构异常钩子。'''

    global _app, _installed, _notifier, _previous_excepthook, _previous_thread_hook
    if _installed:
        return
    _app = app or QApplication.instance()
    _notifier = _NoticeRelay()
    _notifier.notice.connect(_show_notice, Qt.QueuedConnection)
    _installed = True
    _previous_excepthook = sys.excepthook
    _previous_thread_hook = getattr(threading, 'excepthook', None)
    sys.excepthook = _handle_exception
    threading.excepthook = _handle_thread_exception
    if hasattr(sys, 'unraisablehook'):
        sys.unraisablehook = _handle_unraisable


def _format_exception(exc_type, exc_value, exc_tb):
    try:
        return ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:
        return f'{exc_type!r}: {exc_value!r}'


def _handle_exception(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        if _previous_excepthook is not None:
            _previous_excepthook(exc_type, exc_value, exc_tb)
        return
    _report(exc_type, exc_value, exc_tb)


def _handle_thread_exception(args):
    if issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
        if _previous_thread_hook is not None:
            _previous_thread_hook(args)
        return
    name = getattr(args.thread, 'name', '') if args.thread is not None else ''
    _report(args.exc_type, args.exc_value, args.exc_traceback, thread_name=name)


def _handle_unraisable(args):
    try:
        logger.error(
            '未处理的对象异常：%s\n%s',
            getattr(args, 'err_msg', '') or '',
            _format_exception(
                getattr(args, 'exc_type', Exception),
                getattr(args, 'exc_value', None),
                getattr(args, 'exc_traceback', None),
            ),
        )
    except Exception:
        pass


def _report(exc_type, exc_value, exc_tb, thread_name=''):
    suffix = f'（线程：{thread_name}）' if thread_name else ''
    try:
        logger.error(
            '未捕获异常%s：%s',
            suffix,
            _format_exception(exc_type, exc_value, exc_tb),
        )
    except Exception:
        pass

    memory_error = isinstance(exc_value, MemoryError)
    if memory_error:
        _release_memory()
        message = '启动器内存不足，已回收缓存；请关闭占用内存的程序后重试'
    else:
        message = '启动器已拦截一个异常，操作可能未完成；详细原因已写入日志'
    _schedule_notice(message)


def _release_memory():
    try:
        gc.collect()
    except Exception:
        pass
    try:
        from PySide6.QtGui import QPixmapCache
        QPixmapCache.clear()
    except Exception:
        pass


def _schedule_notice(message):
    global _notice_pending, _last_notice_at
    if _notice_pending or _app is None:
        return
    now = time.monotonic()
    # 连续异常时不要刷屏，也不要让提示本身阻塞事件循环。
    if now - _last_notice_at < 8.0:
        return
    _last_notice_at = now
    _notice_pending = True
    try:
        _notifier.notice.emit(message)
    except Exception:
        _notice_pending = False


def _show_notice(message):
    global _notice_pending
    _notice_pending = False
    try:
        if _app is None or _app.closingDown():
            return
        window = _app.activeWindow()
        if window is None:
            for candidate in _app.topLevelWidgets() if _app is not None else ():
                if candidate.isVisible():
                    window = candidate
                    break
        if window is None or not window.isVisible():
            return
        from ui.widgets.toast import show_toast
        show_toast(message, 'error', window)
    except Exception:
        try:
            logger.warning('显示异常提示失败\n%s', traceback.format_exc())
        except Exception:
            pass
