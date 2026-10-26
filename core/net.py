'''
共享网络工具 - 线程安全的 HTTP 会话、流式下载、哈希校验
所有后台 Worker 都应各自创建 HttpClient 实例，禁止跨线程共用 requests.Session。
'''

from __future__ import annotations
import hashlib
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import (Any, Callable, Dict, Iterable, Optional)
from urllib.parse import urlparse
import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
USER_AGENT = 'PMGL-Launcher/1.1 (Minecraft Launcher)'
_NETWORK_STATE_LOCK = threading.Lock()
_NETWORK_STATE = (0.0, True)

def is_network_error_text(text):
    '''判断错误文本是否属于网络连接类问题。'''

    value = str(text or '').lower()
    if not value:
        return False
    markers = ('connection', 'connect', 'timed out', 'timeout', 'max retries', 'getaddrinfo', 'name or service not known', 'temporary failure in name resolution', 'network is unreachable', 'no route to host', 'connection reset', 'remote end closed', 'ssl', 'certificate', 'proxy', 'dns', '10054', '10060', '10061', '11001', '连接', '网络', '超时', '无法连接', '无法访问', '证书', '代理', '未连接到互联网', '断网')
    return any((marker in value for marker in markers))

def is_network_error(exc):
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, requests.exceptions.ProxyError, requests.exceptions.SSLError, socket.gaierror, ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, OSError):
        winerror = getattr(exc, 'winerror', None)
        if winerror in (10050, 10051, 10054, 10060, 10061, 11001):
            return True
    return is_network_error_text(exc)

def network_available(timeout=0.8):
    '''快速检查本机是否真的能访问外网，结果短时间缓存。'''

    global _NETWORK_STATE
    now = time.time()
    with _NETWORK_STATE_LOCK:
        checked_at, cached = _NETWORK_STATE
        if now - checked_at < 3.0:
            return cached
        available = False
    try:
        if sys.platform == 'win32':
            import ctypes
            connected = ctypes.windll.wininet.InternetGetConnectedState(None, 0)
            if not connected:
                available = False
            else:
                available = _tcp_probe(timeout)
        else:
            available = _tcp_probe(timeout)
    except Exception:
        available = False
    with _NETWORK_STATE_LOCK:
        _NETWORK_STATE = time.time(), available
    return available

def _tcp_probe(timeout):
    for host, port in (('223.5.5.5', 53), ('www.baidu.com', 443)):
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return True
        except OSError:
            pass
    return False

class DownloadCancelled(Exception):
    '''用户取消下载'''

class DownloadError(Exception):
    '''下载或校验失败'''

class DownloadBusyError(DownloadError):
    '''同时下载任务数超过全局设置上限'''

class SlowDownloadError(DownloadError):
    '''当前下载源速度过慢，应切换到下一个镜像。'''

class DownloadGate:
    '''全局下载并发闸门，上限来自全局设置 download_threads。'''

    _lock = threading.Lock()
    _condition = threading.Condition(_lock)
    _active = 0
    _max = 64
    @classmethod
    def configure(cls, limit):
        try:
            limit = int(limit)
        except Exception:
            limit = 64
        with cls._condition:
            cls._max = max(1, min(256, limit))
            cls._condition.notify_all()

    @classmethod
    def acquire(cls):
        with cls._condition:
            while cls._active >= cls._max:
                cls._condition.wait(timeout=0.5)
            cls._active += 1

    @classmethod
    def release(cls):
        with cls._condition:
            cls._active = max(0, cls._active - 1)
            cls._condition.notify()

    @classmethod
    def available(cls):
        with cls._lock:
            return max(0, cls._max - cls._active)

    @classmethod
    def max_threads(cls):
        with cls._lock:
            return cls._max

    @classmethod
    @contextmanager
    def slot(cls):
        cls.acquire()
        try:
            yield None
        finally:
            cls.release()


class DownloadSpeedLimiter:
    '''全局下载限速（MB/s，0 表示无限制）。'''

    _lock = threading.Lock()
    _rate = 0.0
    _next_time = 0.0
    @classmethod
    def configure(cls, megabytes_per_second):
        try:
            value = float(megabytes_per_second)
        except (TypeError, ValueError):
            value = 0.0
        with cls._lock:
            cls._rate = max(0.0, value) * 1024 * 1024
            cls._next_time = 0.0

    @classmethod
    def throttle(cls, byte_count, cancel=None):
        with cls._lock:
            rate = cls._rate
            if rate <= 0 or byte_count <= 0:
                return
            now = time.monotonic()
            if cls._next_time < now:
                cls._next_time = now
            cls._next_time += byte_count / rate
            deadline = cls._next_time
        while time.monotonic() < deadline:
            if cancel and cancel():
                with cls._lock:
                    cls._next_time = time.monotonic()
                raise DownloadCancelled()
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


class HttpClient:
    def __init__(self, timeout=20, retries=3, user_agent=USER_AGENT):
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': user_agent, 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.5', 'Connection': 'keep-alive'})
        retry = Retry(total=retries, connect=retries, read=retries, backoff_factor=0.8, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset(['GET', 'HEAD']), raise_on_status=False)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        try:
            from core.config import ConfigManager
            config = ConfigManager()
            DownloadGate.configure(config.get('download_threads', 64))
            DownloadSpeedLimiter.configure(config.get('download_speed_limit', 0))
        except Exception:
            pass

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass

    def get(self, url, *, params=None, headers=None, timeout=None, allow_redirects=True):
        last_error = None
        for attempt in range(self.retries):
            try:
                resp = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout or self.timeout,
                    verify=certifi.where(),
                    allow_redirects=allow_redirects,
                )
                if resp.status_code >= 400:
                    # 401/403/404 不重试，避免无意义等待
                    if resp.status_code in (401, 403, 404):
                        error = DownloadError(
                            f'HTTP {resp.status_code}: {resp.reason}'
                        )
                        error.status_code = resp.status_code
                        raise error
                    last_error = requests.HTTPError(
                        f'HTTP {resp.status_code}: {resp.reason}', response=resp
                    )
                    if attempt < self.retries - 1:
                        time.sleep(0.8 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(0.8 * (attempt + 1))
        if last_error:
            raise DownloadError(str(last_error))
        raise DownloadError('网络请求失败')

    def get_json(self, url, params=None, headers=None, timeout=None):
        resp = self.get(url, params=params, headers=headers, timeout=timeout)
        try:
            return resp.json()
        except ValueError as exc:
            raise DownloadError(f'接口返回的不是有效 JSON: {url}') from exc

    def download(self, url, dest, *, sha1=None, sha256=None, sha512=None, size=None, progress=None, cancel=None, chunk_size=131072, headers=None, allow_slow_fallback=None):
        '''流式下载到 dest，受全局下载线程数限制。

        allow_slow_fallback=False 时关闭“官方源速度慢自动切换镜像”，
        适合安装器这类体积小、镜像未必提供的文件。
        '''

        dest = Path(dest)
        if dest.exists() and self._verify(dest, sha1=sha1, sha256=sha256, sha512=sha512, size=size):
            return False
        try:
            from core.config import ConfigManager
            auto_source = str(ConfigManager().get('file_download_source', 'auto') or 'auto').lower() == 'auto'
        except Exception:
            auto_source = True
        if auto_source:
            host = (urlparse(url).hostname or '').lower()
            auto_source = host.endswith('mojang.com') or host.endswith('minecraft.net') or host in {'maven.minecraftforge.net', 'maven.neoforged.net', 'maven.fabricmc.net'}
        if allow_slow_fallback is not None:
            auto_source = bool(allow_slow_fallback)
        with DownloadGate.slot():
            return self._download_stream(url, dest, sha1=sha1, sha256=sha256, sha512=sha512, size=size, progress=progress, cancel=cancel, chunk_size=chunk_size, slow_fallback=auto_source, extra_headers=headers)

    def _download_stream(self, url, dest, *, sha1=None, sha256=None, sha512=None, size=None, progress=None, cancel=None, chunk_size=131072, slow_fallback=False, extra_headers=None):
        '''流式下载到 dest，支持校验与取消。返回 True 表示确实写入了文件。'''

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and self._verify(dest, sha1=sha1, sha256=sha256, sha512=sha512, size=size):
            return False
        tmp = dest.with_name(dest.name + '.part')
        last_error = None
        for attempt in range(self.retries):
            if cancel and cancel():
                raise DownloadCancelled()
            resume_from = tmp.stat().st_size if tmp.exists() else 0
            request_headers = {'Range': f'bytes={resume_from}-'} if resume_from else {}
            if extra_headers:
                request_headers.update(extra_headers)
            try:
                resp = self.session.get(url, headers=request_headers or None, stream=True, timeout=(8, 12) if slow_fallback else self.timeout, verify=certifi.where(), allow_redirects=True)
                if resp.status_code == 416 and tmp.exists():
                    tmp.unlink(missing_ok=True)
                    continue
                resp.raise_for_status()
                total = int(resp.headers.get('Content-Length') or 0)
                if resp.status_code == 206 and total:
                    total += tmp.stat().st_size if tmp.exists() else 0
                elif not total and size:
                    total = size
                mode = 'ab' if resp.status_code == 206 and tmp.exists() else 'wb'
                done = tmp.stat().st_size if mode == 'ab' and tmp.exists() else 0
                check_time = time.time()
                check_done = done
                read_chunk = min(chunk_size, 16384) if slow_fallback else chunk_size
                with open(tmp, mode) as fh:
                    for chunk in resp.iter_content(chunk_size=read_chunk):
                        if cancel and cancel():
                            raise DownloadCancelled()
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        DownloadSpeedLimiter.throttle(len(chunk), cancel)
                        if progress:
                            progress(done, total)
                        if slow_fallback and total and done < total:
                            now = time.time()
                            elapsed = now - check_time
                            if elapsed >= 6:
                                speed = (done - check_done) / elapsed
                                if speed < 30720:
                                    raise SlowDownloadError('官方源下载速度过慢，已切换到镜像源')
                                check_time = now
                                check_done = done
                if not self._verify(tmp, sha1=sha1, sha256=sha256, sha512=sha512, size=size):
                    tmp.unlink(missing_ok=True)
                    raise DownloadError('文件校验失败（哈希或大小不匹配）')
                os.replace(tmp, dest)
                if progress:
                    final_total = total or (dest.stat().st_size if dest.exists() else 0)
                    progress(final_total, final_total)
                return True
            except DownloadCancelled:
                tmp.unlink(missing_ok=True)
                raise
            except SlowDownloadError:
                raise
            except (requests.RequestException, DownloadError, OSError) as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(1.0 * (attempt + 1))
        tmp.unlink(missing_ok=True)
        if last_error:
            raise DownloadError(str(last_error))
        raise DownloadError('下载失败')

    @staticmethod
    def _verify(path, *, sha1=None, sha256=None, sha512=None, size=None):
        try:
            if not path.is_file():
                return False
            if size is not None and path.stat().st_size != size:
                return False
            if sha1:
                return _hash_file(path, 'sha1') == sha1.lower()
            if sha256:
                return _hash_file(path, 'sha256') == sha256.lower()
            if sha512:
                return _hash_file(path, 'sha512') == sha512.lower()
            return True
        except OSError:
            return False


def _hash_file(path, algorithm):
    h = hashlib.new(algorithm)
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()

def hash_file(path, algorithm='sha1'):
    return _hash_file(Path(path), algorithm)

def sanitize_filename(name, fallback='download'):
    name = (name or '').strip().replace('/', '_').replace('\\', '_')
    for ch in '<>:"|?*\x00':
        name = name.replace(ch, '_')
    name = name.strip(' .')
    return name[:180] or fallback

def unique_path(directory, filename):
    '''若文件已存在，自动追加 (1)、(2) ...'''

    directory = Path(directory)
    path = directory / filename
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 1000):
        candidate = directory / f'{stem} ({i}){suffix}'
        if not candidate.exists():
            return candidate
    return directory / f'{stem}_{int(time.time())}{suffix}'

# WARNING: Decompyle incomplete
