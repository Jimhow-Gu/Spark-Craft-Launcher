'''
下载提供者与文件补全核心。

设计参考 HMCL 的下载架构（DownloadProvider / DownloadCandidate / CacheRepository /
FileDownloadTask），按 Python 多线程场景独立实现：统一候选源、内容校验、
跨游戏目录复用、原子写入与失败回退。
'''

from __future__ import annotations

import os
import platform
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from core.config import ConfigManager
from core.net import (DownloadCancelled, DownloadError, DownloadGate, HttpClient, hash_file, is_network_error_text)

BMCLAPI = 'https://bmclapi2.bangbang93.com'
OPENBMCLAPI = 'https://openbmclapi.bangbang93.com'
MOJANG_LIBS = 'https://libraries.minecraft.net'
MOJANG_RESOURCES = 'https://resources.download.minecraft.net'
FORGE_MAVEN = 'https://maven.minecraftforge.net'
NEOFORGE_MAVEN = 'https://maven.neoforged.net/releases'
FABRIC_MAVEN = 'https://maven.fabricmc.net'
QUILT_MAVEN = 'https://maven.quiltmc.org/repository/release'
FORGE_MAVEN_MIRRORS = ('https://repo.cleanroommc.com/releases', 'https://maven.creeperhost.net')
FORGE_VERSION_MANIFEST = 'https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml'


def current_source():
    try:
        return str(ConfigManager().get('file_download_source', 'auto') or 'auto').lower()
    except Exception:
        return 'auto'


def unique(values):
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def ordered_candidates(official, mirrors, source=None):
    '''按下载源偏好排列候选地址；不丢弃任何来源，保证失败时可回退。'''

    official_values = unique([official] if isinstance(official, str) else list(official or []))
    mirror_values = unique(list(mirrors or []))
    source = (source or current_source() or 'auto').lower()
    if source == 'bmclapi':
        return unique(mirror_values + official_values)
    if source == 'official':
        return unique(official_values + mirror_values)
    return unique(official_values + mirror_values)


def rewrite_mojang_url(url, mirror=BMCLAPI):
    if not url:
        return url
    replacements = (
        ('https://piston-meta.mojang.com', mirror),
        ('https://launchermeta.mojang.com', mirror),
        ('https://launcher.mojang.com', mirror),
        ('https://piston-data.mojang.com', mirror),
        ('https://resources.download.minecraft.net', f'{mirror}/assets'),
        ('http://resources.download.minecraft.net', f'{mirror}/assets'),
        ('https://libraries.minecraft.net', f'{mirror}/maven'),
    )
    for old, new in replacements:
        if url.startswith(old):
            return new + url[len(old):]
    return url


def maven_path(name, classifier='', extension='jar'):
    parts = str(name or '').split('@', 1)
    extension = parts[1] if len(parts) > 1 else extension
    coord = parts[0].split(':')
    if len(coord) < 3:
        return ''
    group, artifact, version = coord[0], coord[1], coord[2]
    filename = f'{artifact}-{version}'
    if classifier:
        filename += f'-{classifier}'
    filename += f'.{extension}'
    return '/'.join(group.split('.')) + f'/{artifact}/{version}/{filename}'


def library_applies(lib):
    rules = lib.get('rules')
    if not rules:
        return True
    os_name = 'windows' if platform.system() == 'Windows' else 'osx' if platform.system() == 'Darwin' else 'linux'
    is_64 = platform.architecture()[0] == '64bit'
    allowed = False

    def matches(rule):
        for key, value in (rule.get('os') or {}).items():
            if key == 'name' and value != os_name:
                return False
            if key == 'arch' and value == 'x86' and is_64:
                return False
            if key == 'arch' and value in ('x86_64', 'amd64') and not is_64:
                return False
        return True

    for rule in rules:
        if not matches(rule):
            continue
        if rule.get('action') == 'disallow':
            return False
        if rule.get('action') == 'allow':
            allowed = True
    return allowed


def version_manifest_urls(source=None):
    official = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'
    mirrors = [
        f'{BMCLAPI}/mc/game/version_manifest_v2.json',
        f'{OPENBMCLAPI}/mc/game/version_manifest_v2.json',
    ]
    return ordered_candidates(official, mirrors, source)


def version_json_urls(version_id, source=None):
    '''官方没有按版本 ID 直接取 JSON 的接口，完全依赖 BMCLAPI 元数据。'''

    mirrors = [
        f'{BMCLAPI}/version/{version_id}/json',
        f'{OPENBMCLAPI}/version/{version_id}/json',
    ]
    return ordered_candidates('', mirrors, source)


def fetch_version_json(version_id, http=None, source=None, timeout=20):
    '''优先 BMCLAPI/OpenBMCLAPI，最后回退到官方 manifest 条目。'''

    http = http or HttpClient(timeout=timeout, retries=2)
    errors = []
    for url in version_json_urls(version_id, source):
        try:
            data = http.get_json(url, timeout=timeout)
        except Exception as exc:
            errors.append(str(exc))
            continue
        if isinstance(data, dict) and data.get('id'):
            return data
    for manifest_url in version_manifest_urls(source):
        try:
            manifest = http.get_json(manifest_url, timeout=timeout)
            entry = next((v for v in manifest.get('versions', []) if v.get('id') == version_id), None)
            if not entry or not entry.get('url'):
                continue
            for candidate in (entry['url'], rewrite_mojang_url(entry['url']), rewrite_mojang_url(entry['url'], OPENBMCLAPI)):
                try:
                    data = http.get_json(candidate, timeout=timeout)
                except Exception as exc:
                    errors.append(str(exc))
                    continue
                if isinstance(data, dict) and data.get('id'):
                    return data
        except Exception as exc:
            errors.append(str(exc))
    raise DownloadError(f'无法获取版本 {version_id} 的 JSON 元数据：{errors[-1] if errors else "未知错误"}')


def client_urls(version_id, downloads=None, source=None):
    client = (downloads or {}).get('client') or {}
    sha1 = str(client.get('sha1') or '')
    urls = []
    official = str(client.get('url') or '')
    if official:
        urls.append(official)
        if 'piston-data.mojang.com' in official:
            urls.append(official.replace('piston-data.mojang.com', 'launcher.mojang.com'))
    mirrors = [
        f'{BMCLAPI}/version/{version_id}/client',
        f'{BMCLAPI}/versions/{version_id}/{version_id}.jar',
        f'{OPENBMCLAPI}/version/{version_id}/client',
        f'{OPENBMCLAPI}/versions/{version_id}/{version_id}.jar',
    ]
    if sha1:
        mirrors.extend([
            f'{OPENBMCLAPI}/v1/objects/{sha1}/client.jar',
            f'{BMCLAPI}/v1/objects/{sha1}/client.jar',
        ])
    return ordered_candidates(urls, mirrors, source)


def asset_object_urls(sha1, source=None):
    sha1 = str(sha1 or '').lower()
    if not sha1:
        return []
    official = f'{MOJANG_RESOURCES}/{sha1[:2]}/{sha1}'
    mirrors = [
        f'{BMCLAPI}/assets/{sha1[:2]}/{sha1}',
        f'{OPENBMCLAPI}/assets/{sha1[:2]}/{sha1}',
    ]
    return ordered_candidates(official, mirrors, source)


def asset_index_urls(asset_index, source=None):
    url = str((asset_index or {}).get('url') or '')
    mirrors = [
        rewrite_mojang_url(url),
        rewrite_mojang_url(url, OPENBMCLAPI),
    ]
    return ordered_candidates(url, mirrors, source)


def log_config_urls(info, source=None):
    url = str((info or {}).get('url') or '')
    return ordered_candidates(url, [rewrite_mojang_url(url), rewrite_mojang_url(url, OPENBMCLAPI)], source)


def library_urls(lib, classifier='', source=None):
    '''根据 library 描述生成主地址和镜像候选。'''

    name = str(lib.get('name') or '')
    downloads = lib.get('downloads') or {}
    if classifier:
        artifact = (downloads.get('classifiers') or {}).get(classifier) or {}
    else:
        artifact = downloads.get('artifact') or {}
    path = str(artifact.get('path') or '') or maven_path(name, classifier)
    if not path:
        return []
    base = str(lib.get('url') or '').rstrip('/')
    official = str(artifact.get('url') or '') or (f'{base}/{path}' if base else f'{MOJANG_LIBS}/{path}')
    return maven_path_urls(path, official, source)


def maven_path_urls(path, official='', source=None):
    '''为任意 maven 相对路径生成官方与镜像候选。'''

    path = str(path or '').lstrip('/')
    if not path:
        return []
    official = str(official or '')
    if not official:
        if path.startswith('net/minecraftforge/'):
            official = f'{FORGE_MAVEN}/{path}'
        elif path.startswith('net/neoforged/'):
            official = f'{NEOFORGE_MAVEN}/{path}'
        elif path.startswith('net/fabricmc/'):
            official = f'{FABRIC_MAVEN}/{path}'
        else:
            official = f'{MOJANG_LIBS}/{path}'
    host = (urlparse(official).hostname or '').lower()
    mirrors = []
    if host in ('maven.minecraftforge.net', 'files.minecraftforge.net'):
        mirrors = [f'{base_mirror}/{path}' for base_mirror in FORGE_MAVEN_MIRRORS]
        mirrors += [f'{BMCLAPI}/maven/{path}', f'{OPENBMCLAPI}/maven/{path}']
    elif host == 'maven.neoforged.net':
        mirrors = [f'{base_mirror}/{path}' for base_mirror in FORGE_MAVEN_MIRRORS]
        mirrors += [f'{BMCLAPI}/maven/{path}', f'{OPENBMCLAPI}/maven/{path}']
    elif host in ('maven.fabricmc.net', 'maven.quiltmc.org', 'libraries.minecraft.net'):
        mirrors = [f'{BMCLAPI}/maven/{path}', f'{OPENBMCLAPI}/maven/{path}']
    else:
        mirrors = [
            rewrite_mojang_url(official),
            rewrite_mojang_url(official, OPENBMCLAPI),
            f'{BMCLAPI}/maven/{path}',
        ]
    return ordered_candidates(official, mirrors, source)


def forge_companion_urls(forge_version, artifact):
    path = f'net/minecraftforge/{artifact}/{forge_version}/{artifact}-{forge_version}.jar'
    official = f'{FORGE_MAVEN}/{path}'
    return ordered_candidates(official, [f'{base}/{path}' for base in FORGE_MAVEN_MIRRORS] + [f'{BMCLAPI}/maven/{path}'])


def forge_universal_urls(full_version):
    full_version = str(full_version)
    path = f'net/minecraftforge/forge/{full_version}/forge-{full_version}-universal.jar'
    return maven_path_urls(path, f'{FORGE_MAVEN}/{path}')


def forge_installer_urls(mc_version, forge_version, prefer_official=False):
    full_version = f'{mc_version}-{forge_version}' if not str(forge_version).startswith(f'{mc_version}-') else str(forge_version)
    path = f'net/minecraftforge/forge/{full_version}/forge-{full_version}-installer.jar'
    official = f'{FORGE_MAVEN}/{path}'
    mirrors = [
        f'{BMCLAPI}/forge/download?mcversion={mc_version}&version={forge_version}&category=installer&format=jar',
        f'{OPENBMCLAPI}/forge/download?mcversion={mc_version}&version={forge_version}&category=installer&format=jar',
    ]
    mirrors += [f'{base}/{path}' for base in FORGE_MAVEN_MIRRORS]
    if prefer_official:
        return ordered_candidates(official, mirrors, 'official')
    return ordered_candidates(official, mirrors, 'bmclapi')


def neoforge_installer_urls(mc_version, neoforge_version, prefer_official=False):
    version = str(neoforge_version)
    path = f'net/neoforged/neoforge/{version}/neoforge-{version}-installer.jar'
    official = f'{NEOFORGE_MAVEN}/{path}'
    mirrors = [f'{base}/{path}' for base in FORGE_MAVEN_MIRRORS]
    if str(mc_version).startswith('1.20.1'):
        old = f'net/neoforged/forge/{mc_version}-{version}/forge-{mc_version}-{version}-installer.jar'
        official = f'{NEOFORGE_MAVEN}/{old}'
        mirrors = [f'{base}/{old}' for base in FORGE_MAVEN_MIRRORS]
    return ordered_candidates(official, mirrors, 'official' if prefer_official else 'auto')


def fabric_profile_urls(mc_version, loader_version):
    official = f'https://meta.fabricmc.net/v2/versions/loader/{mc_version}/{loader_version}/profile/json'
    mirrors = [
        f'{BMCLAPI}/fabric-meta/v2/versions/loader/{mc_version}/{loader_version}/profile/json',
        f'{OPENBMCLAPI}/fabric-meta/v2/versions/loader/{mc_version}/{loader_version}/profile/json',
    ]
    return ordered_candidates(official, mirrors)


def quilt_profile_urls(mc_version, loader_version):
    official = f'https://meta.quiltmc.org/v3/versions/loader/{mc_version}/{loader_version}/profile/json'
    mirrors = [
        f'{BMCLAPI}/quilt-meta/v3/versions/loader/{mc_version}/{loader_version}/profile/json',
        f'{OPENBMCLAPI}/quilt-meta/v3/versions/loader/{mc_version}/{loader_version}/profile/json',
    ]
    return ordered_candidates(official, mirrors)


@dataclass
class RemoteFile:
    '''一个待校验/待下载的远程文件；与 HMCL 的 FileDownloadTask 对应。'''

    label: str
    dest: Path
    urls: List[str] = field(default_factory=list)
    sha1: str = ''
    size: Optional[int] = None
    optional: bool = False
    category: str = 'other'
    check_zip: bool = False

    def valid(self, mode='full'):
        try:
            if not self.dest.is_file():
                return False
            stat = self.dest.stat()
            if mode == 'missing':
                return stat.st_size > 0
            if self.size is not None and stat.st_size != self.size:
                return False
            if stat.st_size <= 0:
                return False
            if mode == 'fast':
                return True
            if self.sha1:
                if hash_file(self.dest, 'sha1') != self.sha1.lower():
                    return False
            if self.check_zip and self.dest.suffix.lower() in ('.jar', '.zip'):
                if not zipfile.is_zipfile(self.dest):
                    return False
            return True
        except OSError:
            return False


@dataclass
class DownloadResult:
    checked: int = 0
    fixed: int = 0
    failed: int = 0
    failed_items: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def summary(self):
        text = f'下载完成：检查 {self.checked} 个文件，补全 {self.fixed} 个'
        if self.failed:
            text += f'，失败 {self.failed} 个'
        return text


class DownloadClient:
    '''线程安全的候选源下载器；每个线程独立 HttpClient，避免 Session 跨线程。'''

    def __init__(self, mc_dir=None, *, source=None, fast=False, missing_only=False,
                 timeout=20, retries=2, file_deadline=0, reuse_game_dirs=True, rounds=2):
        self.mc_dir = Path(mc_dir) if mc_dir else None
        self.source = (source or current_source() or 'auto').lower()
        self.fast = bool(fast)
        self.missing_only = bool(missing_only)
        self.timeout = timeout
        self.retries = retries
        self.file_deadline = max(0, int(file_deadline or 0))
        self.reuse_game_dirs = bool(reuse_game_dirs)
        self.rounds = max(1, int(rounds or 1))
        self._local = threading.local()
        self._clients = []
        self._clients_lock = threading.Lock()
        self._other_dirs = None
        self._host_lock = threading.Lock()
        self._host_failures = {}
        self._host_down_until = {}

    @property
    def mode(self):
        if self.missing_only:
            return 'missing'
        if self.fast:
            return 'fast'
        return 'full'

    def client(self):
        client = getattr(self._local, 'client', None)
        if client is None:
            client = HttpClient(timeout=self.timeout, retries=self.retries)
            self._local.client = client
            with self._clients_lock:
                self._clients.append(client)
        return client

    def close(self):
        with self._clients_lock:
            clients, self._clients = self._clients, []
        for client in clients:
            try:
                client.close()
            except Exception:
                pass

    def _game_dirs(self):
        if self._other_dirs is not None:
            return self._other_dirs
        current = ''
        try:
            current = os.path.normcase(os.path.normpath(str(self.mc_dir.resolve())))
        except OSError:
            current = os.path.normcase(os.path.normpath(str(self.mc_dir)))
        dirs = []
        try:
            configured = ConfigManager().get('game_dirs', []) or []
        except Exception:
            configured = []
        for root in configured:
            try:
                key = os.path.normcase(os.path.normpath(str(Path(root).resolve())))
            except OSError:
                key = os.path.normcase(os.path.normpath(str(root)))
            if key and key != current:
                dirs.append(Path(root))
        self._other_dirs = dirs
        return dirs

    def _copy_from_other_dirs(self, remote, mode):
        if not (self.reuse_game_dirs and self.mc_dir):
            return False
        try:
            relative = remote.dest.relative_to(self.mc_dir)
        except ValueError:
            return False
        if not relative.parts or relative.parts[0] not in ('versions', 'libraries', 'assets'):
            return False
        for root in self._game_dirs():
            source = Path(root) / relative
            if not source.is_file():
                continue
            try:
                if remote.size is not None and source.stat().st_size != remote.size:
                    continue
                if mode == 'full' and remote.sha1:
                    if hash_file(source, 'sha1') != remote.sha1.lower():
                        continue
                remote.dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, remote.dest)
                return True
            except OSError:
                continue
        return False

    def _candidate_order(self, urls):
        '''同一主机连续失败后暂时排到候选末尾，避免每个文件都重试被墙的官方源。'''

        now = time.monotonic()
        with self._host_lock:
            down = dict(self._host_down_until)

        def rank(url):
            host = (urlparse(url).hostname or '').lower()
            return 1 if down.get(host, 0.0) > now else 0

        return sorted(unique(urls), key=rank)

    def _mark_host_failure(self, url, error):
        if not is_network_error_text(str(error)):
            return
        host = (urlparse(url).hostname or '').lower()
        if not host:
            return
        with self._host_lock:
            count = self._host_failures.get(host, 0) + 1
            self._host_failures[host] = count
            if count >= 2:
                self._host_down_until[host] = time.monotonic() + 300.0

    def _mark_host_success(self, url):
        host = (urlparse(url).hostname or '').lower()
        if not host:
            return
        with self._host_lock:
            self._host_failures.pop(host, None)
            self._host_down_until.pop(host, None)

    def download(self, remote, *, cancel=None, progress=None, deadline=None):
        if cancel and cancel():
            raise DownloadCancelled()
        mode = self.mode
        if remote.valid(mode):
            return False
        if self._copy_from_other_dirs(remote, mode):
            return True
        if not remote.urls:
            raise DownloadError(f'{remote.label}: 无可用下载地址')
        if not deadline and self.file_deadline:
            deadline = time.time() + self.file_deadline
        last_error = ''
        for round_index in range(self.rounds):
            urls = self._candidate_order(remote.urls)
            if round_index % 2 == 1:
                urls = list(reversed(urls))
            for url in urls:
                if cancel and cancel():
                    raise DownloadCancelled()
                if deadline and time.time() > deadline:
                    last_error = '下载超时'
                    break

                def file_cancel():
                    return bool(cancel and cancel()) or bool(deadline and time.time() > deadline)

                try:
                    self.client().download(
                        url, remote.dest, sha1=remote.sha1 or None, size=remote.size,
                        progress=progress, cancel=file_cancel,
                    )
                except DownloadCancelled:
                    if cancel and cancel():
                        raise
                    last_error = '下载超时'
                    continue
                except Exception as exc:
                    last_error = str(exc)
                    self._mark_host_failure(url, exc)
                    continue
                self._mark_host_success(url)
                if remote.dest.is_file() and remote.dest.stat().st_size > 0:
                    return True
                last_error = '下载结果为空'
            if round_index + 1 < self.rounds:
                if cancel and cancel():
                    raise DownloadCancelled()
                if deadline and time.time() > deadline:
                    break
                time.sleep(min(1.5, 0.45 * (round_index + 1)))
        raise DownloadError(f'{remote.label}: {last_error or "下载失败"}')

    def download_many(self, files, *, workers=None, cancel=None, progress=None, on_file_done=None, on_progress=None):
        files = list(files)
        result = DownloadResult()
        result.checked = len(files)
        total = max(1, len(files))
        done = 0
        done_lock = threading.Lock()

        def work(remote):
            if cancel and cancel():
                raise DownloadCancelled()
            if self.fast and remote.optional:
                return remote, True, False, ''
            def file_progress(current, file_total):
                if on_progress is not None:
                    try:
                        on_progress(remote, current, file_total)
                    except Exception:
                        pass
            try:
                changed = self.download(remote, cancel=cancel, progress=file_progress)
                return remote, True, changed, ''
            except DownloadCancelled:
                raise
            except Exception as exc:
                return remote, False, False, str(exc)

        if not files:
            return result
        worker_count = max(1, min(DownloadGate.max_threads(), int(workers or DownloadGate.max_threads()), total))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {pool.submit(work, remote): remote for remote in files}
            for future in as_completed(futures):
                if cancel and cancel():
                    for pending in futures:
                        pending.cancel()
                    raise DownloadCancelled()
                try:
                    remote, ok, changed, error = future.result()
                except DownloadCancelled:
                    for pending in futures:
                        pending.cancel()
                    raise
                except Exception as exc:
                    remote = futures[future]
                    ok, changed, error = False, False, str(exc)
                if ok:
                    if changed:
                        result.fixed += 1
                elif remote.optional:
                    result.warnings.append(f'{remote.label}: {error}')
                else:
                    result.failed += 1
                    if len(result.failed_items) < 10:
                        result.failed_items.append(remote.label)
                if on_file_done is not None:
                    try:
                        on_file_done(remote, ok, changed, error)
                    except Exception:
                        pass
                with done_lock:
                    done += 1
                    current = done
                if progress is not None:
                    try:
                        progress(current, total)
                    except Exception:
                        pass
        return result


__all__ = [
    'BMCLAPI', 'OPENBMCLAPI', 'MOJANG_LIBS', 'MOJANG_RESOURCES', 'FORGE_MAVEN_MIRRORS',
    'DownloadClient', 'DownloadResult', 'RemoteFile',
    'asset_index_urls', 'asset_object_urls', 'client_urls', 'fabric_profile_urls',
    'fetch_version_json', 'forge_companion_urls', 'forge_installer_urls', 'forge_universal_urls',
    'library_applies', 'library_urls', 'log_config_urls', 'maven_path', 'maven_path_urls',
    'ordered_candidates', 'quilt_profile_urls', 'rewrite_mojang_url',
    'version_json_urls', 'version_manifest_urls',
]
