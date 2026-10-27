'''全局下载/修复任务状态管理。'''

from __future__ import annotations
import threading
import time
from typing import (Dict, List, Optional)
from PySide6.QtCore import QObject, Signal

class DownloadManager(QObject):
    changed = Signal()
    _instance: "Optional['DownloadManager']" = None
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._tasks = {}
        self._order = []
        self._cancel_callbacks = {}
        self._emit_lock = threading.Lock()
        self._last_emit = 0.0

    def _emit_changed(self, force=False):
        '''限制进度刷新频率，避免高频信号拖慢主界面。'''

        now = time.monotonic()
        with self._emit_lock:
            if not force and now - self._last_emit < 0.08:
                return
            self._last_emit = now
        self.changed.emit()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = DownloadManager()
        return cls._instance

    def start_task(self, task_id, title, steps=None, cancel_callback=None):
        with self._lock:
            self._tasks[task_id] = {'id': task_id, 'title': title, 'percent': 0.5, 'status': '准备中...', 'speed': '0 B/s', 'remaining': '0', 'success': None, 'message': '', 'done': False, 'created': time.time(), 'steps': [{'text': str(item.get('text', '')) if isinstance(item, dict) else str(item), 'state': item.get('state', 'pending') if isinstance(item, dict) else 'pending', 'percent': float(item.get('percent', 0)) if isinstance(item, dict) else 0.0} for item in steps or []]}
            if task_id not in self._order:
                self._order.append(task_id)
            if cancel_callback is not None:
                self._cancel_callbacks[task_id] = cancel_callback
        self._emit_changed(force=True)

    def update_task(self, task_id, **kwargs):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            for key, value in kwargs.items():
                if key in task:
                    task[key] = value
        self._emit_changed()

    def set_cancel_callback(self, task_id, callback):
        '''任务创建后再绑定取消回调（例如先创建 Worker 再启动任务）。'''

        changed = False
        with self._lock:
            if task_id in self._tasks and callback is not None:
                self._cancel_callbacks[task_id] = callback
                changed = True
        # 不能在持锁时 emit：UI 槽会回调 task()/tasks()，同一把非重入锁会自锁
        if changed:
            self._emit_changed(force=True)

    def update_step(self, task_id, index, text=None, state=None, percent=None):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or index < 0 or index >= len(task['steps']):
                return
            step = task['steps'][index]
            if text is not None:
                step['text'] = text
            if state is not None:
                step['state'] = state
            if percent is not None:
                step['percent'] = max(0.0, min(100.0, float(percent)))
        self._emit_changed()

    def finish_task(self, task_id, success, message=''):
        removed = False
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if success or '取消' in (message or ''):
                self._tasks.pop(task_id, None)
                self._cancel_callbacks.pop(task_id, None)
                if task_id in self._order:
                    self._order.remove(task_id)
                removed = True
            else:
                task['done'] = True
                task['success'] = False
                task['message'] = message
                task['status'] = message or '失败'
                for step in task['steps']:
                    if step['state'] == 'active':
                        step['state'] = 'error'
        if removed or not success:
            self._emit_changed(force=True)

    def remove_task(self, task_id):
        with self._lock:
            self._tasks.pop(task_id, None)
            self._cancel_callbacks.pop(task_id, None)
            if task_id in self._order:
                self._order.remove(task_id)
        self._emit_changed(force=True)

    def cancel_task(self, task_id):
        '''关闭任务；如果仍在进行中，同时通知对应 worker 取消。'''

        callback = None
        with self._lock:
            task = self._tasks.pop(task_id, None)
            callback = self._cancel_callbacks.pop(task_id, None)
            if task_id in self._order:
                self._order.remove(task_id)
        self._emit_changed(force=True)
        if callback is not None:
            try:
                callback()
            except Exception:
                pass

    def task(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def tasks(self):
        with self._lock:
            return [dict(self._tasks[tid]) for tid in self._order if tid in self._tasks]

    def active_tasks(self):
        return [task for task in self.tasks() if not task.get('done')]

    def snapshot(self):
        tasks = self.active_tasks()
        if not tasks:
            failed = [t for t in self.tasks() if t.get('done') if not t.get('success')]
            if failed:
                first = failed[-1]
                return {'active': False, 'failed': True, 'count': len(failed), 'percent': float(first.get('percent', 0)), 'title': f'{first.get("title", "任务")} 失败', 'status': first.get('message', ''), 'speed': '0 B/s', 'remaining': '0'}
            return {'active': False, 'failed': False, 'count': 0, 'percent': 0.0, 'title': '', 'status': '', 'speed': '0 B/s', 'remaining': '0'}
        percent = sum((float(t.get('percent', 0)) for t in tasks)) / max(1, len(tasks))
        first = tasks[0]
        title = first.get('title', '下载任务')
        if len(tasks) > 1:
            title = f'{title} 等 {len(tasks)} 个任务'
        return {'active': True, 'failed': any((not t.get('success') for t in self.tasks() if not t.get('done'))), 'count': len(tasks), 'percent': percent, 'title': title, 'status': first.get('status', ''), 'speed': first.get('speed', '0 B/s'), 'remaining': first.get('remaining', '0')}


