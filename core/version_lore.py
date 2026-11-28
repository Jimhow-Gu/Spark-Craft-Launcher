"""愚人节版本文案注册表：seed + 本地缓存 + 异步远端刷新。"""

from __future__ import annotations

import atexit
import json
import logging
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from core.net import HttpClient
from core.paths import scl_dir
from core.version_text import release_year

logger = logging.getLogger('VersionLore')

MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 1000
MAX_TEXT = 300
CACHE_TTL = 6 * 3600
DEFAULT_URLS = (
    'https://gh-proxy.com/https://raw.githubusercontent.com/'
    'Jimhow-Gu/Spark-Craft-Launcher/main/data/fool_versions.json',
    'https://raw.githubusercontent.com/'
    'Jimhow-Gu/Spark-Craft-Launcher/main/data/fool_versions.json',
)


def _project_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))


def seed_path():
    return _project_root() / 'data' / 'fool_versions.json'


def cache_path():
    return scl_dir() / 'cache' / 'fool_versions.json'


def normalize_id(value):
    return str(value or '').strip().lower()


def validate_payload(data):
    """校验文案 JSON；返回只含 schema/updated/entries 的规范化数据。"""

    if not isinstance(data, dict) or data.get('schema') != 1:
        return None
    rows = data.get('entries')
    if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
        return None
    entries = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        version_id = normalize_id(row.get('id'))
        text = str(row.get('text') or '').strip()
        if not version_id or not text:
            continue
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT]
            logger.warning('愚人节文案过长，已截断: %s', version_id)
        aliases = []
        raw_aliases = row.get('aliases')
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                alias = normalize_id(alias)
                if alias and alias != version_id and alias not in aliases:
                    aliases.append(alias)
        key = (version_id, tuple(aliases))
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            'id': version_id,
            'aliases': aliases,
            'text': text,
        })
    return {
        'schema': 1,
        'updated': str(data.get('updated') or ''),
        'entries': entries,
    }


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    return validate_payload(data)


class FoolLoreFetchWorker(QThread):
    loaded = Signal(dict)
    unchanged = Signal()
    failed = Signal(str)

    def __init__(self, urls, *, etag='', last_modified='', parent=None):
        super().__init__(parent)
        self.urls = list(urls or ())
        self.etag = str(etag or '')
        self.last_modified = str(last_modified or '')

    def _write_cache(self, payload):
        target = cache_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix('.tmp')
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            os.replace(temp, target)
        except OSError as exc:
            logger.warning('愚人节文案缓存写入失败: %s', exc)

    def run(self):
        last_error = ''
        for url in self.urls:
            if not str(url).startswith('https://'):
                continue
            http = HttpClient(timeout=8, retries=2)
            try:
                headers = {}
                if self.etag:
                    headers['If-None-Match'] = self.etag
                if self.last_modified:
                    headers['If-Modified-Since'] = self.last_modified
                response = http.get(
                    str(url),
                    headers=headers or None,
                    timeout=8,
                )
                if response.status_code == 304:
                    self.unchanged.emit()
                    return
                if len(response.content or b'') > MAX_BYTES:
                    last_error = '文案文件超过 1MB'
                    continue
                payload = validate_payload(response.json())
                if payload is None:
                    last_error = '文案 JSON 结构无效'
                    continue
                payload['fetched_at'] = time.time()
                payload['etag'] = str(response.headers.get('ETag') or '')
                payload['last_modified'] = str(
                    response.headers.get('Last-Modified') or ''
                )
                self._write_cache(payload)
                self.loaded.emit(payload)
                return
            except Exception as exc:
                last_error = str(exc)
            finally:
                http.close()
        self.failed.emit(last_error or '没有可用的文案地址')


class FoolLoreRegistry(QObject):
    updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._texts = {}
        self._ids = set()
        self._loaded = False
        self._worker = None
        self._cache_meta = {}
        self._quit_hooked = False

    def _apply(self, payload):
        if not payload:
            return
        texts = {}
        ids = set()
        for row in payload.get('entries') or ():
            version_id = normalize_id(row.get('id'))
            text = str(row.get('text') or '').strip()
            if not version_id or not text:
                continue
            texts[version_id] = text
            ids.add(version_id)
            for alias in row.get('aliases') or ():
                alias = normalize_id(alias)
                if alias:
                    ids.add(alias)
        self._texts = texts
        self._ids = ids

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        seed = _read_json(seed_path())
        if seed:
            self._apply(seed)
        cached_raw = None
        try:
            cached_raw = json.loads(
                cache_path().read_text(encoding='utf-8')
            )
        except (OSError, ValueError, TypeError):
            cached_raw = None
        if isinstance(cached_raw, dict):
            self._cache_meta = {
                'fetched_at': float(cached_raw.get('fetched_at') or 0),
                'etag': str(cached_raw.get('etag') or ''),
                'last_modified': str(
                    cached_raw.get('last_modified') or ''
                ),
            }
        cached = validate_payload(cached_raw)
        if cached:
            self._apply(cached)

    def _configured_urls(self):
        urls = []
        try:
            from core.config import ConfigManager
            config = ConfigManager()
            raw = config.get('version_lore_urls')
            if isinstance(raw, (list, tuple)):
                urls.extend(str(item) for item in raw)
            else:
                one = str(config.get('version_lore_url') or '').strip()
                if one:
                    urls.append(one)
        except Exception:
            pass
        urls.extend(DEFAULT_URLS)
        result = []
        seen = set()
        for url in urls:
            url = str(url or '').strip()
            if not url.startswith('https://') or url in seen:
                continue
            seen.add(url)
            result.append(url)
        return result

    def start(self):
        self._ensure_loaded()
        app = None
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
        except Exception:
            pass
        if app is not None and not self._quit_hooked:
            app.aboutToQuit.connect(self.stop)
            self._quit_hooked = True
        if self._worker is not None and self._worker.isRunning():
            return
        fetched_at = float(self._cache_meta.get('fetched_at') or 0)
        if fetched_at and time.time() - fetched_at < CACHE_TTL:
            return
        self._worker = FoolLoreFetchWorker(
            self._configured_urls(),
            etag=self._cache_meta.get('etag', ''),
            last_modified=self._cache_meta.get('last_modified', ''),
            parent=self,
        )
        worker = self._worker
        worker.loaded.connect(self._on_loaded)
        worker.unchanged.connect(self._on_unchanged)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(
            lambda w=worker: self._on_worker_finished(w)
        )
        worker.start()

    def _on_loaded(self, payload):
        old = dict(self._texts)
        old_ids = set(self._ids)
        self._apply(payload)
        self._cache_meta = {
            'fetched_at': float(payload.get('fetched_at') or 0),
            'etag': str(payload.get('etag') or ''),
            'last_modified': str(payload.get('last_modified') or ''),
        }
        if self._texts != old or self._ids != old_ids:
            self.updated.emit()

    def _on_unchanged(self):
        now = time.time()
        self._cache_meta['fetched_at'] = now

    def _on_failed(self, message):
        text = str(message or '')
        if '404' in text:
            logger.info('愚人节文案远端尚未发布: %s', text)
        else:
            logger.warning('愚人节文案刷新失败: %s', text)

    def _on_worker_finished(self, worker):
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def stop(self):
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.wait(1500)
            if worker.isRunning():
                worker.terminate()
                worker.wait(300)
        except RuntimeError:
            pass

    def entries(self):
        self._ensure_loaded()
        return dict(self._texts)

    def is_fool_id(self, version_id):
        self._ensure_loaded()
        return normalize_id(version_id) in self._ids

    def lore_text(self, version, date_text=''):
        self._ensure_loaded()
        if not isinstance(version, dict):
            return ''
        version_id = normalize_id(version.get('id'))
        text = self._texts.get(version_id, '')
        if not text:
            return ''
        year = release_year(version)
        return f'{year} | {text}' if year else text


fool_registry = FoolLoreRegistry()
atexit.register(fool_registry.stop)

__all__ = [
    'FoolLoreFetchWorker', 'FoolLoreRegistry', 'cache_path',
    'fool_registry', 'normalize_id', 'seed_path', 'validate_payload',
]
