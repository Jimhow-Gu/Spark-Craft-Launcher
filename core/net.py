'''
共享网络工具 - 线程安全的 HTTP 会话、流式下载、哈希校验
所有后台 Worker 都应各自创建 HttpClient 实例，禁止跨线程共用 requests.Session。
'''

from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
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

def network_available(timeout=0.35):
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


def _part_meta_path(part):
    return Path(str(part) + '.meta')


def _read_part_meta(part):
    path = _part_meta_path(part)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_part_meta(part, data):
    path = _part_meta_path(part)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False), encoding='utf-8'
        )
    except OSError:
        pass


def _remove_part_files(part):
    try:
        Path(part).unlink(missing_ok=True)
    except OSError:
        pass
    try:
        _part_meta_path(part).unlink(missing_ok=True)
    except OSError:
        pass


class HttpClient:
    def __init__(self, timeout=20, retries=3, user_agent=USER_AGENT, verify=True):
        self.timeout = timeout
        self.retries = retries
        # 设置页的“验证 HTTPS 证书”是全局开关；默认开启，显式关闭才绕过校验。
        if verify is True:
            try:
                from core.config import ConfigManager
                verify = bool(ConfigManager().get('verify_ssl', True))
            except Exception:
                pass
        self._verify_certs = certifi.where() if verify else False
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': user_agent, 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.5', 'Connection': 'keep-alive'})
        # 重试统一由上层 HttpClient/候选源逻辑控制，避免 urllib3 再乘一次重试，
        # 在坏网络下产生几十倍连接尝试。
        adapter = HTTPAdapter(max_retries=0, pool_connections=8, pool_maxsize=8)
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
                    verify=self._verify_certs,
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
            _remove_part_files(dest.with_name(dest.name + '.part'))
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
            _remove_part_files(dest.with_name(dest.name + '.part'))
            return False
        tmp = dest.with_name(dest.name + '.part')
        last_error = None
        for attempt in range(self.retries):
            if cancel and cancel():
                raise DownloadCancelled()
            resume_from = tmp.stat().st_size if tmp.exists() else 0
            part_meta = _read_part_meta(tmp) if resume_from else {}
            if resume_from:
                # .part 必须绑定到同一个 URL / 版本；没有任何校验依据时
                # 禁止续传，直接全量重下，避免复用其它文件的半包。
                expected_hashes = {
                    key: str(value or '').lower()
                    for key, value in (
                        ('sha1', sha1), ('sha256', sha256), ('sha512', sha512)
                    )
                    if value
                }
                meta_hashes = {
                    key: str(part_meta.get(key) or '').lower()
                    for key in ('sha1', 'sha256', 'sha512')
                    if part_meta.get(key)
                }
                validators = bool(
                    part_meta.get('etag') or part_meta.get('last_modified')
                )
                if (
                    not part_meta
                    or str(part_meta.get('url') or '') != url
                    or any(
                        expected_hashes.get(key)
                        and meta_hashes.get(key)
                        and expected_hashes[key] != meta_hashes[key]
                        for key in ('sha1', 'sha256', 'sha512')
                    )
                    or (not validators and not expected_hashes and not meta_hashes)
                ):
                    _remove_part_files(tmp)
                    resume_from = 0
                    part_meta = {}
            request_headers = {'Range': f'bytes={resume_from}-'} if resume_from else {}
            if extra_headers:
                request_headers.update(extra_headers)
            try:
                resp = self.session.get(url, headers=request_headers or None, stream=True, timeout=(8, 12) if slow_fallback else self.timeout, verify=self._verify_certs, allow_redirects=True)
                if resp.status_code == 416 and tmp.exists():
                    _remove_part_files(tmp)
                    continue
                resp.raise_for_status()
                total = int(resp.headers.get('Content-Length') or 0)
                content_range = str(resp.headers.get('Content-Range') or '')
                if resp.status_code == 206:
                    # 服务器必须从 .part 现有长度继续；否则会把错误偏移的数据
                    # 追加到文件里。Content-Range 缺失时保留旧兼容行为。
                    match = re.match(
                        r'bytes\s+(\d+)-(\d+)/(\d+|\*)',
                        content_range.strip(),
                        re.I,
                    )
                    if match:
                        start = int(match.group(1))
                        if start != resume_from:
                            _remove_part_files(tmp)
                            raise DownloadError(
                                f'续传位置不匹配：期望 {resume_from}，'
                                f'服务器返回 {start}'
                            )
                        if match.group(3) != '*':
                            total = int(match.group(3))
                        elif total:
                            total += resume_from
                    elif total:
                        total += resume_from
                elif not total and size:
                    total = size
                response_etag = str(resp.headers.get('ETag') or '')
                response_last_modified = str(resp.headers.get('Last-Modified') or '')
                if resume_from and resp.status_code == 206 and part_meta:
                    old_etag = str(part_meta.get('etag') or '')
                    old_modified = str(part_meta.get('last_modified') or '')
                    if (
                        (old_etag and response_etag and old_etag != response_etag)
                        or (old_modified and response_last_modified
                            and old_modified != response_last_modified)
                    ):
                        _remove_part_files(tmp)
                        raise DownloadError('远端文件已变化，已放弃旧 .part 并重新下载')
                _write_part_meta(tmp, {
                    'url': url,
                    'etag': response_etag,
                    'last_modified': response_last_modified,
                    'total_size': total,
                    'sha1': str(sha1 or ''),
                    'sha256': str(sha256 or ''),
                    'sha512': str(sha512 or ''),
                })
                mode = 'ab' if resp.status_code == 206 and tmp.exists() else 'wb'
                done = tmp.stat().st_size if mode == 'ab' and tmp.exists() else 0
                # Content-Length 可能比调用方传入的 size 更准确；写入前
                # 再检查一次目标盘，未知大小时保留最低安全余量。
                free_now = None
                try:
                    free_now = shutil.disk_usage(str(dest.parent)).free
                except OSError:
                    pass
                if free_now is not None:
                    remaining = max(0, total - done) if total else 0
                    if total and free_now < remaining:
                        raise DownloadError(
                            '磁盘空间不足：需要约 %.2f GB，当前剩余 %.2f GB'
                            % (remaining / (1024 ** 3), free_now / (1024 ** 3))
                        )
                    if not total and free_now < 64 * 1024 * 1024:
                        raise DownloadError(
                            '磁盘空间不足：当前剩余 %.1f MB'
                            % (free_now / (1024 ** 2))
                        )
                check_time = time.time()
                check_done = done
                disk_check_done = done
                read_chunk = min(chunk_size, 16384) if slow_fallback else chunk_size
                with open(tmp, mode) as fh:
                    for chunk in resp.iter_content(chunk_size=read_chunk):
                        if cancel and cancel():
                            raise DownloadCancelled()
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        if not total and done - disk_check_done >= 8 * 1024 * 1024:
                            disk_check_done = done
                            try:
                                free_after = shutil.disk_usage(str(dest.parent)).free
                            except OSError:
                                free_after = None
                            if free_after is not None and free_after < 64 * 1024 * 1024:
                                raise DownloadError(
                                    '磁盘空间不足：当前剩余 %.1f MB'
                                    % (free_after / (1024 ** 2))
                                )
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
                actual_size = tmp.stat().st_size
                # 0 字节是合法文件（例如空的 marker/占位文件）；只有服务器
                # 明确返回失败或调用方期望非空时才拒绝。
                if actual_size == 0 and (size is None or int(size) != 0):
                    if total not in (0, None):
                        tmp.unlink(missing_ok=True)
                        raise DownloadError('下载结果为空')
                if not self._verify(tmp, sha1=sha1, sha256=sha256, sha512=sha512, size=size):
                    _remove_part_files(tmp)
                    raise DownloadError('文件校验失败（哈希或大小不匹配）')
                os.replace(tmp, dest)
                try:
                    _part_meta_path(tmp).unlink(missing_ok=True)
                except OSError:
                    pass
                if progress:
                    final_total = total or (dest.stat().st_size if dest.exists() else 0)
                    progress(final_total, final_total)
                return True
            except DownloadCancelled:
                _remove_part_files(tmp)
                raise
            except SlowDownloadError:
                raise
            except (requests.RequestException, DownloadError, OSError) as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(1.0 * (attempt + 1))
        _remove_part_files(tmp)
        if last_error:
            raise DownloadError(str(last_error))
        raise DownloadError('下载失败')

    @staticmethod
    def _verify(path, *, sha1=None, sha256=None, sha512=None, size=None):
        try:
            if not path.is_file():
                return False
            actual_size = path.stat().st_size
            if size is not None and actual_size != size:
                return False
            checked = False
            for algorithm, expected in (
                ('sha1', sha1),
                ('sha256', sha256),
                ('sha512', sha512),
            ):
                if not expected:
                    continue
                checked = True
                if _hash_file(path, algorithm) != str(expected).lower():
                    return False
            if checked:
                return True
            if size is not None:
                return True
            # 没有任何期望哈希/大小时，0 字节文件不可能是有效下载结果。
            return actual_size > 0
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
