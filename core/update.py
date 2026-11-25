'''启动器版本检查与自动更新。'''

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.net import DownloadCancelled, DownloadError, HttpClient, hash_file
from core.paths import safe_rmtree, scl_dir
from core.release_info import (
    CURRENT_VERSION, VERSION_DISPLAY, EXECUTABLE_BUILD,
)
REPO = 'Jimhow-Gu/Spark-Craft-Launcher'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
API_KGGITHUB = f'https://api.kkgithub.com/repos/{REPO}/releases/latest'
API_ACCELERATED = f'https://gh-proxy.com/{API_LATEST}'
# Release 元数据并发查询官方接口和可用镜像，避免单个接口超时拖慢检查。
API_SOURCES = (
    API_KGGITHUB,
    API_LATEST,
    API_ACCELERATED,
)
# GitHub 下载加速线路。不同机房对镜像的连通性差异很大，尽量多留回退。
DOWNLOAD_ACCELERATORS = (
    'https://gh-proxy.com/',
    'https://ghfast.top/',
    'https://ghproxy.cc/',
    'https://ghproxy.net/',
)


def _download_candidates(info):
    '''生成更新附件的候选下载地址和所需请求头。'''

    info = dict(info or {})
    browser_url = str(info.get('asset_url') or '').strip()
    api_url = str(info.get('asset_api_url') or '').strip()
    candidates = []
    seen = set()

    def add(url, headers=None):
        url = str(url or '').strip()
        if not url or url in seen:
            return
        seen.add(url)
        candidates.append((url, dict(headers or {})))

    # releases/download 页面通常由镜像直接缓存，先走这条路线；随后再用
    # GitHub API 的 asset 接口兜底（它返回二进制流而不是 HTML 错误页）。
    if browser_url.startswith(('https://github.com/', 'http://github.com/', 'https://objects.githubusercontent.com/')):
        for prefix in DOWNLOAD_ACCELERATORS:
            add(prefix + browser_url)

    if api_url:
        add(
            'https://gh-proxy.com/' + api_url,
            {'Accept': 'application/octet-stream'},
        )
        if api_url.startswith('https://api.github.com/'):
            add(
                api_url.replace('https://api.github.com/', 'https://api.kkgithub.com/', 1),
                {'Accept': 'application/octet-stream'},
            )
        add(api_url, {'Accept': 'application/octet-stream'})

    if browser_url:
        add(browser_url)
    return candidates


def _probe_update_candidates(candidates, timeout=4):
    """用 Range 读取一小段测吞吐，按速度排序；失败候选保留原顺序垫底。"""

    candidates = list(candidates or [])
    if len(candidates) <= 2:
        return candidates
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def probe(item):
        url, headers = item
        client = None
        try:
            client = HttpClient(timeout=timeout, retries=1)
            request_headers = dict(headers or {})
            request_headers['Range'] = 'bytes=0-262143'
            start = time.monotonic()
            resp = client.session.get(
                url,
                headers=request_headers,
                stream=True,
                timeout=(3, 5),
                verify=client._verify_certs,
                allow_redirects=True,
            )
            if resp.status_code >= 400:
                return item, 0.0
            data = resp.raw.read(262144, decode_content=True) or b''
            elapsed = max(0.001, time.monotonic() - start)
            return item, len(data) / elapsed
        except Exception:
            return item, 0.0
        finally:
            if client is not None:
                client.close()

    ranked = []
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(candidates)))) as pool:
        futures = [pool.submit(probe, item) for item in candidates]
        for future in as_completed(futures):
            try:
                ranked.append(future.result())
            except Exception:
                continue
    if not ranked:
        return candidates
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return [item for item, speed in ranked if speed > 0] + [
        item for item, speed in ranked if speed <= 0
    ]


def _download_urls(url):
    '''兼容旧调用：仅返回 URL，保留官方源作为最后回退。'''

    info = {'asset_url': str(url or '')}
    return [item[0] for item in _download_candidates(info)]



def _version_tuple(text):
    numbers = [int(part) for part in re.findall(r'\d+', str(text or ''))]
    return tuple(numbers[:4])


def is_newer(latest_tag, current=CURRENT_VERSION):
    latest = _version_tuple(latest_tag)
    current_tuple = _version_tuple(current)
    length = max(len(latest), len(current_tuple))
    return latest + (0,) * (length - len(latest)) > current_tuple + (0,) * (length - len(current_tuple))


def _parse_dll_build(body):
    match = re.search(r'(?im)^\s*(?:DLL[\s_-]*BUILD|DLL版号)\s*[:=：]\s*(\d+)', str(body or ''))
    try:
        return int(match.group(1)) if match else 0
    except (TypeError, ValueError):
        return 0


def update_kind(info, current=CURRENT_VERSION, current_dll=EXECUTABLE_BUILD):
    '''返回 version / dll / none。版本号优先，版本相同时比较 DLL 构建号。'''

    if not isinstance(info, dict):
        return 'none'
    latest = str(info.get('version') or info.get('tag') or '').strip()
    if not latest:
        return 'none'
    if is_newer(latest, current):
        return 'version'
    if _version_tuple(latest) != _version_tuple(current):
        return 'none'
    try:
        remote_dll = int(info.get('dll_build') or 0)
    except (TypeError, ValueError):
        remote_dll = 0
    try:
        local_dll = int(current_dll or 0)
    except (TypeError, ValueError):
        local_dll = 0
    return 'dll' if remote_dll > local_dll else 'none'


def is_unpublished_build(info, current=CURRENT_VERSION, current_dll=EXECUTABLE_BUILD):
    '''当前运行版本是否比 GitHub 上已发布的正式版还新（意外流出的构建）。'''

    if not isinstance(info, dict):
        return False
    latest = str(info.get('version') or info.get('tag') or '').strip()
    if not latest:
        return False
    if is_newer(current, latest):
        return True
    if _version_tuple(latest) != _version_tuple(current):
        return False
    try:
        remote_dll = int(info.get('dll_build') or 0)
        local_dll = int(current_dll or 0)
    except (TypeError, ValueError):
        return False
    # Release 正文没有 DLL 构建号时无法判断，不能据此误报。
    return remote_dll > 0 and local_dll > remote_dll


def _fetch_release_api(api_url, timeout, results, stop=None):
    http = None
    try:
        if stop is not None and stop.is_set():
            return
        http = HttpClient(timeout=timeout, retries=1)
        data = http.get_json(
            api_url,
            headers={
                'Accept': 'application/vnd.github+json',
                'User-Agent': 'Spark-Craft-Launcher-Updater',
            },
        )
        if stop is not None and stop.is_set():
            return
        if isinstance(data, dict) and data.get('tag_name'):
            results.put((api_url, data, None))
            return
        results.put((api_url, None, DownloadError('更新接口没有返回有效的 Release 信息')))
    except Exception as exc:
        results.put((api_url, None, exc))
    finally:
        if http is not None:
            http.close()


def is_onedir_install():
    """当前进程是否为 PyInstaller onedir 安装（用于选择目录更新包）。"""

    if not getattr(sys, 'frozen', False):
        return False
    try:
        return (Path(sys.executable).resolve().parent / '_internal').is_dir()
    except OSError:
        return False


def _asset_priority(assets, prefer_onedir):
    usable = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get('name') or '').strip()
        if not name:
            continue
        lowered = name.lower()
        # Release 里保留了历史 .bak/.old 备份，旧版更新器只按顺序取第一个
        # exe；显式跳过备份，避免新客户端下载到旧构建。
        if any(marker in lowered for marker in ('.bak', 'backup', '.old')):
            continue
        usable.append(asset)
    if prefer_onedir:
        for asset in usable:
            name = str(asset.get('name') or '').lower()
            if name.endswith('.zip') and 'onedir' in name:
                return asset
        for asset in usable:
            name = str(asset.get('name') or '').lower()
            if name.endswith('.zip') and 'setup' not in name and 'installer' not in name:
                return asset
    else:
        # 旧版 onefile 只能替换单个 exe。安装器 exe 不能作为自动更新附件，
        # 否则旧客户端会把启动器本体换成安装程序。
        for asset in usable:
            name = str(asset.get('name') or '').lower()
            if name.endswith('.exe') and not any(
                marker in name for marker in ('setup', 'installer', '安装')
            ):
                return asset
        for asset in usable:
            name = str(asset.get('name') or '').lower()
            if name.endswith('.zip') and 'onedir' not in name:
                return asset
    return None


def fetch_latest_release(timeout=12, prefer_onedir=None):
    """并发读取 Release，取版本最高/构建号最大的结果，避免镜像旧缓存抢先。"""

    sources = list(dict.fromkeys(API_SOURCES))
    results = queue.Queue()
    stop = threading.Event()
    for api_url in sources:
        threading.Thread(
            target=_fetch_release_api,
            args=(api_url, timeout, results, stop),
            daemon=True,
            name='SCL-UpdateMeta',
        ).start()

    payloads = []
    last_error = None
    deadline = time.monotonic() + max(6.0, float(timeout) + 2.0)
    first_valid_at = None
    pending = len(sources)
    while pending > 0:
        remaining = deadline - time.monotonic()
        if first_valid_at is not None:
            remaining = min(remaining, first_valid_at + 1.5 - time.monotonic())
        if remaining <= 0:
            break
        try:
            _api_url, payload, error = results.get(timeout=remaining)
        except queue.Empty:
            break
        pending -= 1
        if isinstance(payload, dict) and payload.get('tag_name'):
            payloads.append(payload)
            if first_valid_at is None:
                first_valid_at = time.monotonic()
            continue
        if error is not None:
            last_error = error
    stop.set()
    if not payloads:
        if last_error is not None:
            raise last_error
        return None

    def rank(payload):
        tag = str(payload.get('tag_name') or '')
        return (
            _version_tuple(tag),
            _parse_dll_build(payload.get('body')),
        )

    data = max(payloads, key=rank)
    assets = data.get('assets') or []
    if prefer_onedir is None:
        prefer_onedir = is_onedir_install()
    chosen = _asset_priority(assets, bool(prefer_onedir))
    tag = str(data.get('tag_name') or '').strip()
    body = str(data.get('body') or '')
    return {
        'tag': tag,
        'version': tag.lstrip('vV'),
        'name': str(data.get('name') or tag),
        'body': body,
        'dll_build': _parse_dll_build(body),
        'html_url': str(data.get('html_url') or ''),
        'published_at': str(data.get('published_at') or ''),
        'asset_name': str((chosen or {}).get('name') or ''),
        'asset_url': str((chosen or {}).get('browser_download_url') or ''),
        'asset_api_url': str((chosen or {}).get('url') or ''),
        'asset_id': int((chosen or {}).get('id') or 0),
        'asset_size': int((chosen or {}).get('size') or 0),
        'asset_digest': str((chosen or {}).get('digest') or ''),
    }


class CheckUpdateWorker(QThread):
    done = Signal(object, str, bool)

    def __init__(self, manual=False, parent=None):
        super().__init__(parent)
        self.manual = bool(manual)

    def run(self):
        try:
            info = fetch_latest_release()
        except Exception as exc:
            self.done.emit(None, _friendly_update_error(exc), self.manual)
            return
        self.done.emit(info, '', self.manual)


def _friendly_update_error(exc):
    text = str(exc or '').strip()
    lower = text.lower()
    if any(marker in lower for marker in ('remote end closed', 'remotedisconnected', 'connection aborted', 'connection reset', '10054')):
        return '连接被远端中断'
    if any(marker in lower for marker in ('timed out', 'timeout', 'read timed out', 'connecttimeout')):
        return '连接超时'
    if any(marker in lower for marker in ('certificate', 'ssl', 'tls')):
        return 'SSL 证书校验失败'
    if any(marker in lower for marker in ('hash', 'size', '校验', '不是有效的 exe')):
        return '更新文件校验失败'
    text = re.sub(r'\s+', ' ', text)
    if len(text) > 140:
        text = text[:137] + '...'
    return text or '未知错误'


class UpdateDownloadWorker(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = dict(info or {})
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        name = str(self.info.get('asset_name') or 'Spark Craft Launcher-update.exe')
        candidates = _download_candidates(self.info)
        if not candidates:
            self.failed.emit('该 Release 没有可下载的 exe/zip 附件')
            return
        if len(candidates) > 2:
            self.status.emit('正在测试更新源速度...')
            candidates = _probe_update_candidates(candidates)
        update_dir = scl_dir() / 'update' / 'downloads'
        update_dir.mkdir(parents=True, exist_ok=True)
        dest = update_dir / re.sub(r'[^A-Za-z0-9._-]', '_', name)
        digest = str(self.info.get('asset_digest') or '')
        sha256 = digest[7:].strip() if digest.lower().startswith('sha256:') else None
        last_error = None
        http = HttpClient(timeout=18, retries=1)
        try:
            for index, (candidate, headers) in enumerate(candidates):
                if self._cancelled:
                    self.failed.emit('更新下载已取消')
                    return
                if index == 0:
                    self.status.emit('正在通过镜像更新源下载...')
                elif index < len(candidates) - 1:
                    self.status.emit(f'当前更新源不可用，正在切换（{index + 1}/{len(candidates)}）...')
                else:
                    self.status.emit('正在尝试 GitHub 官方源...')
                try:
                    http.download(
                        candidate, dest,
                        sha256=sha256,
                        size=int(self.info.get('asset_size') or 0) or None,
                        progress=(lambda done, total: self.progress.emit(int(done), int(total))),
                        cancel=(lambda: self._cancelled),
                        headers=headers or None,
                        allow_slow_fallback=True,
                    )
                    try:
                        write_downloaded_update(self.info, dest)
                    except Exception:
                        pass
                    # 下载完成后直接启动外部更新器并强制退出，完全不依赖界面按钮/定时器。
                    version = str(self.info.get('version') or self.info.get('tag') or '')
                    try:
                        prepare_update(dest, version)
                    except Exception as exc:
                        write_update_log(
                            f'download: external updater failed: {exc}'
                        )
                        # 不要报 done：文件下载成功但 exe 没有被替换，界面必须
                        # 显示失败并保留已下载文件供下次启动重试。
                        data = _read_downloaded_update()
                        attempts = _bump_downloaded_attempts(data) if data else 0
                        if attempts >= 3:
                            clear_downloaded_update()
                            _set_manual_update_notice(
                                '更新安装连续失败 3 次，请手动下载新版或清理 SCL/update 后重试'
                            )
                            detail = '，已连续失败 3 次，请手动下载新版或清理 SCL/update'
                        else:
                            detail = f'，已记录第 {attempts} 次失败，下次启动会重试'
                        self.failed.emit(
                            f'更新已下载，但启动安装失败：{exc}{detail}'
                        )
                        return
                    # 退出前让 GUI 线程释放 QMediaPlayer/音频设备，避免
                    # 更新替换 exe 时仍有 D3D/WASAPI 句柄。
                    try:
                        from PySide6.QtCore import QMetaObject, Qt
                        from PySide6.QtWidgets import QApplication
                        app = QApplication.instance()
                        if app is not None:
                            for widget in app.topLevelWidgets():
                                if hasattr(widget, 'shutdown_media'):
                                    QMetaObject.invokeMethod(
                                        widget, 'shutdown_media', Qt.QueuedConnection
                                    )
                    except Exception:
                        pass
                    threading.Timer(2.0, lambda: os._exit(0)).start()
                    write_update_log('download: external updater armed; exiting in 2s')
                    self.done.emit(str(dest))
                    return
                except DownloadCancelled:
                    self.failed.emit('更新下载已取消')
                    return
                except Exception as exc:
                    last_error = exc
                    write_update_log(
                        f'download source failed: {candidate} '
                        f'{type(exc).__name__}: {exc}'
                    )
                    continue
        finally:
            http.close()
        detail = _friendly_update_error(last_error)
        self.failed.emit(
            f'更新下载失败：{detail}\n'
            f'已尝试 {len(candidates)} 个更新源，请检查网络或稍后重试。'
        )


def _looks_like_pe(path):
    try:
        with open(path, 'rb') as fh:
            head = fh.read(4096)
        if len(head) < 64 or head[:2] != b'MZ':
            return False
        pe_offset = int.from_bytes(head[0x3C:0x40], 'little')
        if pe_offset < 0 or pe_offset + 24 > len(head):
            return False
        if head[pe_offset:pe_offset + 4] != b'PE' + bytes(2):
            return False
        machine = int.from_bytes(head[pe_offset + 4:pe_offset + 6], 'little')
        return machine in (0x014c, 0x8664, 0xAA64)
    except OSError:
        return False


def _extract_exe(asset_path):
    path = Path(asset_path)
    target = scl_dir() / 'update' / 'Spark Craft Launcher.exe.new'
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == '.exe':
        if not _looks_like_pe(path):
            raise DownloadError('下载的更新文件不是有效的 exe')
        shutil.copy2(path, target)
        return target
    if path.suffix.lower() != '.zip':
        raise DownloadError('不支持的更新附件格式')
    with zipfile.ZipFile(path) as zf:
        # 只允许取启动器本体 exe。SCL 是用户运行数据，更新包即使包含
        # SCL/ 也必须忽略，绝不整体覆盖或搬移。
        members = [
            name for name in zf.namelist()
            if Path(name).suffix.lower() == '.exe'
            and 'scl' not in {part.lower() for part in Path(name).parts}
        ]
        member = next((name for name in members if Path(name).name.lower() in ('spark craft launcher.exe', 'sparkcraftlauncher.exe')), '')
        if not member and len(members) == 1:
            member = members[0]
        if not member:
            raise DownloadError('更新 zip 中没有可用的启动器 exe')
        target.write_bytes(zf.read(member))
    if not _looks_like_pe(target):
        raise DownloadError('更新文件不是有效的 exe')
    return target


def _safe_extract_zip(archive, destination):
    '''只按安全相对路径解压，拒绝 zip slip、SCL 用户数据和异常体积。'''

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    total_size = 0
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > 20000:
            raise DownloadError('更新 zip 文件数量异常')
        for info in infos:
            raw_name = info.filename.replace('\\', '/').lstrip('/')
            parts = [part for part in raw_name.split('/') if part not in ('', '.')]
            if not parts or '..' in parts:
                raise DownloadError('更新 zip 包含非法路径')
            if parts[0].lower() == '__macosx':
                continue
            # 任何层级的 SCL 都是用户数据，更新包不得覆盖或搬移。
            if any(part.lower() == 'scl' for part in parts):
                continue
            total_size += int(info.file_size or 0)
            if total_size > 2 * 1024 * 1024 * 1024:
                raise DownloadError('更新 zip 解压后体积异常')
            target = (root.joinpath(*parts)).resolve()
            if target != root and root not in target.parents:
                raise DownloadError('更新 zip 路径越界')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


def _extract_onedir(asset_path):
    '''解压 onedir 更新包，返回 (启动器 exe, 包根目录)。'''

    path = Path(asset_path)
    if not path.is_file():
        raise DownloadError('更新安装包不存在，请重新下载更新。')
    if path.suffix.lower() != '.zip':
        raise DownloadError('onedir 更新需要 zip 安装包')
    destination = scl_dir() / 'update' / 'onedir_new'
    safe_rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    _safe_extract_zip(path, destination)

    candidates = []
    for candidate in destination.rglob('*'):
        try:
            if not candidate.is_file() or candidate.suffix.lower() != '.exe':
                continue
            if not (candidate.parent / '_internal').is_dir():
                continue
            candidates.append(candidate)
        except OSError:
            continue
    preferred = [
        candidate for candidate in candidates
        if candidate.name.lower() in ('sparkcraftlauncher.exe', 'spark craft launcher.exe')
    ]
    chosen = preferred[0] if preferred else (candidates[0] if len(candidates) == 1 else None)
    if chosen is None:
        safe_rmtree(destination)
        raise DownloadError('更新 zip 中没有有效的 onedir 启动器')
    chosen = chosen.resolve()
    try:
        with chosen.open('rb') as handle:
            if handle.read(2) != b'MZ':
                safe_rmtree(destination)
                raise DownloadError('更新包中的启动器不是有效的 exe')
        if not any((chosen.parent / '_internal').iterdir()):
            safe_rmtree(destination)
            raise DownloadError('更新包中的 _internal 为空')
    except OSError as exc:
        raise DownloadError(f'无法读取更新包：{exc}')
    root = chosen.parent
    try:
        if destination.resolve() not in root.parents and root != destination.resolve():
            raise DownloadError('更新包目录越界')
    except OSError as exc:
        raise DownloadError(f'无法读取更新包目录：{exc}')
    return chosen, root


def _prepare_onedir_update(asset_path, version):
    '''解压后由 PowerShell 等待旧进程退出，再整体替换 exe 与 _internal。'''

    if not getattr(sys, 'frozen', False):
        raise DownloadError('源码运行模式不支持自动更新。')
    target = Path(sys.executable).resolve()
    if not target.is_file():
        raise DownloadError('找不到当前启动器 exe，无法自动更新。')
    new_exe, new_root = _extract_onedir(asset_path)
    if new_exe.name.lower() != target.name.lower():
        raise DownloadError('更新包中的启动器名称与当前程序不一致')
    update_dir = scl_dir() / 'update'
    update_dir.mkdir(parents=True, exist_ok=True)
    marker = _pending_update_marker(version)
    updated_marker = _updated_update_path()
    failed_marker = _update_marker_path(FAILED_MARKER)
    raw_parent_pid = int(os.getppid() or 0)
    parent_path = _process_executable_path(raw_parent_pid)
    wait_parent_pid = 0
    if (
        raw_parent_pid
        and raw_parent_pid != os.getpid()
        and _same_executable_path(parent_path, target)
    ):
        wait_parent_pid = raw_parent_pid
    else:
        write_update_log(
            'prepare onedir: parent wait skipped '
            f'pid={raw_parent_pid} path={parent_path!r}'
        )
    parent_arg = parent_path if wait_parent_pid else ''
    script = update_dir / 'apply_onedir_update.ps1'
    script_text = r"""param(
    [int]$WaitPid,
    [int]$ParentPid,
    [string]$ParentPath,
    [string]$NewRoot,
    [string]$TargetExe,
    [string]$WorkDir,
    [string]$Version,
    [string]$UpdatedMarker,
    [string]$FailedMarker,
    [string]$InstallingMarker
)
$TargetDir = Split-Path -Parent $TargetExe
$ExeName = Split-Path -Leaf $TargetExe
$TargetInternal = Join-Path $TargetDir "_internal"
$NewInternal = Join-Path $NewRoot "_internal"
$BackupInternal = $TargetInternal + ".old"
$BackupExe = $TargetExe + ".old"
$LogDir = Join-Path $TargetDir "SCL\\update"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "apply_update.log"

function Write-UpdaterLog([string]$Text) {
    Add-Content -LiteralPath $Log -Value ("[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $Text) -Encoding UTF8
}

function Invoke-Retry([string]$Label, [scriptblock]$Action) {
    for ($i = 0; $i -lt 120; $i++) {
        try {
            & $Action
            return $true
        } catch {
            if (($i % 10) -eq 0) {
                Write-UpdaterLog ($Label + " retry: " + $_.Exception.Message)
            }
            Start-Sleep -Milliseconds 500
        }
    }
    Write-UpdaterLog ($Label + " failed after retries")
    return $false
}

function Write-UpdateMarker([string]$Path, [string]$State, [string]$Reason) {
    $payload = @{ version = $Version; state = $State; time = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
    if ($Reason) { $payload.reason = $Reason }
    $json = $payload | ConvertTo-Json -Compress
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json, $utf8)
}

function Fail([string]$Reason) {
    Write-UpdaterLog ("onedir failed: " + $Reason)
    try { Write-UpdateMarker $FailedMarker "failed" $Reason } catch { }
    Remove-Item -LiteralPath $InstallingMarker -Force -ErrorAction SilentlyContinue
    exit 1
}

function Stop-BackupProcess([string]$Path) {
    if (!$Path) { return }
    try {
        foreach ($proc in @(Get-Process)) {
            try {
                $procPath = $proc.Path
                if ($procPath -and [string]::Equals(
                    [System.IO.Path]::GetFullPath($procPath),
                    [System.IO.Path]::GetFullPath($Path),
                    [System.StringComparison]::OrdinalIgnoreCase
                )) {
                    Write-UpdaterLog ("stopping process holding backup: " + $proc.Id + " " + $procPath)
                    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                }
            } catch { }
        }
    } catch { }
}

function Wait-Or-Kill([int]$ProcessId, [int]$TimeoutSeconds, [string]$Label) {
    if (!$ProcessId -or $ProcessId -le 0) { return $true }
    $process = $null
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
    } catch {
        return $true
    }
    Write-UpdaterLog ("waiting for " + $Label + " pid " + $ProcessId + " timeout=" + $TimeoutSeconds + "s")
    try {
        if ($process.WaitForExit($TimeoutSeconds * 1000)) {
            return $true
        }
    } catch { }
    Write-UpdaterLog ("timeout waiting for " + $Label + " pid " + $ProcessId + "; force stopping")
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
    } catch {
        Write-UpdaterLog ("force stop failed for " + $Label + ": " + $_.Exception.Message)
    }
    try { $process.WaitForExit(5000) | Out-Null } catch { }
    return $true
}

$parentValid = $false
if ($ParentPid -gt 0 -and $ParentPath) {
    try {
        $parentProc = Get-Process -Id $ParentPid -ErrorAction Stop
        $parentExe = $parentProc.Path
        if ($parentExe -and [string]::Equals(
            [System.IO.Path]::GetFullPath($parentExe),
            [System.IO.Path]::GetFullPath($TargetExe),
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            $parentValid = $true
        }
    } catch { }
}
if ($ParentPid -gt 0 -and -not $parentValid) {
    Write-UpdaterLog "parent path check failed; parent will not be waited or killed"
    $ParentPid = 0
}

trap {
    Write-UpdaterLog ("onedir unhandled: " + $_)
    try { Write-UpdateMarker $FailedMarker "failed" "unhandled" } catch { }
    Remove-Item -LiteralPath $InstallingMarker -Force -ErrorAction SilentlyContinue
    exit 1
}

Wait-Or-Kill $WaitPid 45 "old launcher" | Out-Null
Wait-Or-Kill $ParentPid 15 "launcher parent" | Out-Null
Start-Sleep -Milliseconds 750
if (!(Test-Path -LiteralPath $NewInternal) -or !(Test-Path -LiteralPath (Join-Path $NewRoot $ExeName))) {
    Fail "new package incomplete"
}

Stop-BackupProcess $BackupExe
if (Test-Path -LiteralPath $BackupInternal) {
    if (!(Invoke-Retry "remove old internal backup" { Remove-Item -LiteralPath $BackupInternal -Recurse -Force -ErrorAction Stop })) {
        Fail "remove old internal backup"
    }
}
if (Test-Path -LiteralPath $BackupExe) {
    if (!(Invoke-Retry "remove old exe backup" { Remove-Item -LiteralPath $BackupExe -Force -ErrorAction Stop })) {
        Fail "remove old exe backup"
    }
}

$movedExe = $false
$movedInternal = $false
if (Test-Path -LiteralPath $TargetExe) {
    $movedExe = Invoke-Retry "backup exe" { Move-Item -LiteralPath $TargetExe -Destination $BackupExe -Force -ErrorAction Stop }
    if (!$movedExe) { Fail "cannot move current exe" }
}
if (Test-Path -LiteralPath $TargetInternal) {
    $movedInternal = Invoke-Retry "backup internal" { Move-Item -LiteralPath $TargetInternal -Destination $BackupInternal -Force -ErrorAction Stop }
    if (!$movedInternal) {
        if ($movedExe) {
            Invoke-Retry "restore exe" { Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction Stop } | Out-Null
        }
        Fail "cannot move current _internal"
    }
}
$copiedInternal = Invoke-Retry "copy internal" {
    New-Item -ItemType Directory -Force -Path $TargetInternal -ErrorAction Stop | Out-Null
    Copy-Item -Path (Join-Path $NewInternal "*") -Destination $TargetInternal -Recurse -Force -ErrorAction Stop
}
$copiedExe = $false
if ($copiedInternal) {
    $copiedExe = Invoke-Retry "copy exe" { Copy-Item -LiteralPath (Join-Path $NewRoot $ExeName) -Destination $TargetExe -Force -ErrorAction Stop }
}
if ($copiedInternal -and $copiedExe) {
    $verified = $false
    try {
        $sourceHash = (Get-FileHash -LiteralPath (Join-Path $NewRoot $ExeName) -Algorithm SHA256).Hash
        $targetHash = (Get-FileHash -LiteralPath $TargetExe -Algorithm SHA256).Hash
        if ($sourceHash -and $sourceHash -eq $targetHash) { $verified = $true }
    } catch {
        Write-UpdaterLog ("onedir verify failed: " + $_.Exception.Message)
    }
    if (!$verified) {
        Invoke-Retry "rollback internal" { if (Test-Path -LiteralPath $BackupInternal) { Remove-Item -LiteralPath $TargetInternal -Recurse -Force -ErrorAction SilentlyContinue; Move-Item -LiteralPath $BackupInternal -Destination $TargetInternal -Force -ErrorAction Stop } } | Out-Null
        if ($movedExe) {
            Invoke-Retry "rollback exe" { Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction Stop } | Out-Null
        }
        Fail "copied exe hash mismatch"
    }
    $started = Invoke-Retry "start new version" { Start-Process -FilePath $TargetExe -WorkingDirectory $TargetDir -ErrorAction Stop }
    if (!$started) {
        Write-UpdaterLog "onedir: restart failed, rolling back"
        Invoke-Retry "rollback exe" { if (Test-Path -LiteralPath $BackupExe) { Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction Stop } } | Out-Null
        Invoke-Retry "rollback internal" { if (Test-Path -LiteralPath $BackupInternal) { Remove-Item -LiteralPath $TargetInternal -Recurse -Force -ErrorAction SilentlyContinue; Move-Item -LiteralPath $BackupInternal -Destination $TargetInternal -Force -ErrorAction Stop } } | Out-Null
        Fail "restart failed and rolled back"
    }
    Write-UpdaterLog "onedir: updated and restarted"
    try {
        Write-UpdateMarker $UpdatedMarker "updated" ""
        Remove-Item -LiteralPath $InstallingMarker -Force -ErrorAction SilentlyContinue
    } catch {
        Write-UpdaterLog ("onedir updated marker failed: " + $_.Exception.Message)
    }
    $cleanupOk = $true
    Stop-BackupProcess $BackupExe
    if (!(Invoke-Retry "cleanup internal backup" { if (Test-Path -LiteralPath $BackupInternal) { Remove-Item -LiteralPath $BackupInternal -Recurse -Force -ErrorAction Stop } })) {
        $cleanupOk = $false
        Write-UpdaterLog "onedir: internal backup cleanup failed"
    }
    if (!(Invoke-Retry "cleanup exe backup" { if (Test-Path -LiteralPath $BackupExe) { Remove-Item -LiteralPath $BackupExe -Force -ErrorAction Stop } })) {
        $cleanupOk = $false
        Write-UpdaterLog "onedir: exe backup cleanup failed"
    }
    if (!(Invoke-Retry "cleanup extracted package" { if (Test-Path -LiteralPath $NewRoot) { Remove-Item -LiteralPath $NewRoot -Recurse -Force -ErrorAction Stop } })) {
        $cleanupOk = $false
        Write-UpdaterLog "onedir: extracted package cleanup failed"
    }
    if ($cleanupOk) {
        Write-UpdaterLog "onedir: update cleanup complete"
        exit 0
    }
    Write-UpdaterLog "onedir: updated but cleanup incomplete"
    exit 2
}

Write-UpdaterLog "onedir: copy failed, rolling back"
Invoke-Retry "rollback internal" { if (Test-Path -LiteralPath $BackupInternal) { Remove-Item -LiteralPath $TargetInternal -Recurse -Force -ErrorAction SilentlyContinue; Move-Item -LiteralPath $BackupInternal -Destination $TargetInternal -Force -ErrorAction Stop } } | Out-Null
if ($movedExe) {
    Invoke-Retry "rollback exe" { if (Test-Path -LiteralPath $BackupExe) { Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction Stop } } | Out-Null
}
Fail "copy failed after rollback"
"""
    script.write_text(script_text, encoding='utf-8-sig')
    write_update_log(
        f'prepare onedir: target={target} new={new_exe} '
        f'wait_parent={wait_parent_pid} script={script}'
    )
    try:
        subprocess.Popen(
            [
                'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(script),
                str(os.getpid()), str(wait_parent_pid), str(parent_arg),
                str(new_root), str(target), str(target.parent),
                str(version), str(updated_marker), str(failed_marker), str(marker),
            ],
            creationflags=(
                getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            ),
            close_fds=True,
            cwd=str(target.parent),
            env=_clean_child_environment(),
        )
        write_update_log('prepare onedir: powershell started')
    except OSError as exc:
        write_update_log(f'prepare onedir: powershell failed: {exc}')
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return script

def write_update_log(text):
    try:
        log_dir = scl_dir() / 'update'
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        with (log_dir / 'apply_update.log').open('a', encoding='utf-8') as handle:
            handle.write(f'[{stamp}] {text}\n')
    except OSError:
        pass


def _clean_child_environment():
    '''去掉 PyInstaller onefile 的临时目录变量，避免新进程复用即将删除的 _MEI。'''

    env = os.environ.copy()
    for key in list(env):
        upper = key.upper()
        if (
            upper.startswith('_PYI_')
            or upper.startswith('_MEIPASS')
            or upper.startswith('PYINSTALLER_')
        ):
            env.pop(key, None)
    return env


INSTALLING_MARKER = 'pending_update.json'
UPDATED_MARKER = 'updated_update.json'
FAILED_MARKER = 'failed_update.json'


def _update_marker_path(name):
    return scl_dir() / 'update' / name


def _write_update_marker(path, version, state, reason=''):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        'version': str(version),
        'state': str(state),
        'time': time.time(),
    }
    if reason:
        data['reason'] = str(reason)
    path.write_text(
        json.dumps(data, ensure_ascii=False), encoding='utf-8'
    )
    return path


def _pending_update_marker(version):
    '''记录“正在安装”；它不是成功回执。'''

    for name in (UPDATED_MARKER, FAILED_MARKER):
        try:
            _update_marker_path(name).unlink(missing_ok=True)
        except OSError:
            pass
    return _write_update_marker(
        _update_marker_path(INSTALLING_MARKER), version, 'installing'
    )


def _updated_update_marker(version):
    '''记录“复制并校验成功”，只由真实成功路径写入。'''

    return _write_update_marker(
        _update_marker_path(UPDATED_MARKER), version, 'updated'
    )


def _failed_update_marker(version, reason=''):
    return _write_update_marker(
        _update_marker_path(FAILED_MARKER), version, 'failed', reason
    )


DOWNLOADED_MARKER = 'downloaded_update.json'

# 更新后需要重新获取的下载素材；不触碰背景、音乐、皮肤等用户文件。
_CACHE_REFRESH_ITEMS = (
    'mcnews_home.xaml',
    'mcnews_images',
    'mcnews_details',
    'mcnews_detail_images',
    'community_translations.json',
    'optifabric',
)


def _pending_update_path():
    return _update_marker_path(INSTALLING_MARKER)


def _updated_update_path():
    return _update_marker_path(UPDATED_MARKER)


def pending_update_exists():
    '''是否已有成功的更新回执；仅用于更新后的缓存刷新。'''

    return _updated_update_path().is_file()


def clear_launcher_caches():
    '''新版首次启动时清理旧版下载素材，避免继续显示过期缓存。'''

    root = scl_dir() / 'cache'
    cleared = 0
    for name in _CACHE_REFRESH_ITEMS:
        path = root / name
        try:
            if path.is_dir():
                safe_rmtree(path)
                cleared += 1
            elif path.is_file():
                path.unlink(missing_ok=True)
                cleared += 1
        except OSError:
            continue
    write_update_log(f'cache refresh: cleared {cleared} cache items')


def _downloaded_marker_path():
    return scl_dir() / 'update' / DOWNLOADED_MARKER


_MANUAL_UPDATE_NOTICE = ''


def _manual_notice_path():
    return scl_dir() / 'update' / 'manual_update_notice.json'


def _set_manual_update_notice(message):
    global _MANUAL_UPDATE_NOTICE
    message = str(message or '')
    _MANUAL_UPDATE_NOTICE = message
    try:
        path = _manual_notice_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({'message': message, 'time': time.time()}, ensure_ascii=False),
            encoding='utf-8',
        )
    except OSError:
        pass


def consume_manual_update_notice():
    """读取并清除一次性提示；文件保证重启后仍能提示一次。"""

    global _MANUAL_UPDATE_NOTICE
    message = ''
    try:
        path = _manual_notice_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding='utf-8'))
            message = str(data.get('message') or '')
            path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass
    if not message:
        message = _MANUAL_UPDATE_NOTICE
    _MANUAL_UPDATE_NOTICE = ''
    return message


def _bump_downloaded_attempts(data):
    """记录一次安装尝试；返回尝试次数。"""

    try:
        attempts = int(data.get('attempts') or 0) + 1
    except (TypeError, ValueError):
        attempts = 1
    data = dict(data or {})
    data['attempts'] = attempts
    try:
        marker = _downloaded_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(data, ensure_ascii=False), encoding='utf-8'
        )
    except OSError:
        pass
    return attempts


def write_downloaded_update(info, asset_path):
    '''记录已下载完成的更新，供下次启动兜底自动安装。'''

    info = dict(info or {})
    path = Path(asset_path)
    if not path.is_file():
        return
    marker = _downloaded_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    actual_size = path.stat().st_size
    expected_size = int(info.get('asset_size') or 0)
    digest = str(info.get('asset_digest') or '').strip()
    local_sha256 = ''
    try:
        local_sha256 = hash_file(path, 'sha256')
    except Exception:
        pass
    marker.write_text(
        json.dumps({
            'schema': 2,
            'version': str(info.get('version') or info.get('tag') or '').lstrip('vV'),
            'tag': str(info.get('tag') or ''),
            'dll_build': int(info.get('dll_build') or 0),
            'asset_path': str(path),
            # Release 声明的目标大小；缺失时才退回本地大小。
            'asset_size': expected_size or actual_size,
            'asset_digest': digest,
            'asset_sha256': local_sha256,
            'attempts': 0,
            'time': time.time(),
        }, ensure_ascii=False),
        encoding='utf-8',
    )


def _read_downloaded_update():
    marker = _downloaded_marker_path()
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
        # DLL40 及更早的 marker 没有 schema/digest，asset_size 还是本地大小；
        # 不能套用新规则，直接作废并让下载器重新获取。
        if int(data.get('schema') or 0) < 2:
            marker.unlink(missing_ok=True)
            return None
        path = Path(str(data.get('asset_path') or ''))
        if not path.is_file():
            return None
        expected = int(data.get('asset_size') or 0)
        if expected and path.stat().st_size != expected:
            marker.unlink(missing_ok=True)
            return None
        digest = str(data.get('asset_digest') or '').strip().lower()
        local_sha = str(data.get('asset_sha256') or '').strip().lower()
        if not digest and not local_sha:
            marker.unlink(missing_ok=True)
            return None
        expected_sha = ''
        if digest.startswith('sha256:'):
            expected_sha = digest.split(':', 1)[1].strip()
        elif local_sha:
            expected_sha = local_sha
        if expected_sha:
            try:
                actual_sha = hash_file(path, 'sha256')
            except Exception:
                actual_sha = ''
            if actual_sha != expected_sha:
                marker.unlink(missing_ok=True)
                return None
        return data
    except (OSError, ValueError, TypeError):
        return None


def downloaded_update_available():
    return _read_downloaded_update() is not None


def clear_downloaded_update():
    try:
        _downloaded_marker_path().unlink(missing_ok=True)
    except OSError:
        pass
    try:
        _manual_notice_path().unlink(missing_ok=True)
    except OSError:
        pass


def apply_downloaded_update_on_startup():
    '''启动时安装已下载完成的更新；失败则继续正常启动。'''

    data = _read_downloaded_update()
    if not data:
        return False
    try:
        if update_kind(data, current=CURRENT_VERSION, current_dll=EXECUTABLE_BUILD) == 'none':
            clear_downloaded_update()
            return False
    except Exception:
        clear_downloaded_update()
        return False
    attempts = int(data.get('attempts') or 0)
    if attempts >= 3:
        clear_downloaded_update()
        _set_manual_update_notice(
            '更新安装连续失败 3 次，请手动下载新版或清理 SCL/update 后重试'
        )
        return False
    asset = Path(str(data.get('asset_path') or ''))
    version = str(data.get('version') or data.get('tag') or '')
    attempts = _bump_downloaded_attempts(data)
    write_update_log(
        f'startup: applying downloaded update {version} from {asset} '
        f'(attempt {attempts}/3)'
    )
    try:
        if is_onedir_install():
            # onedir 不能只换 exe；外部脚本会等待本进程退出后整体替换。
            # 下载 marker 必须保留到新版启动后按版本号确认，不能在这里
            # 因为“脚本已启动”就当成安装成功。
            prepare_update(asset, version)
            return True
        if apply_update_inplace(asset, version):
            # helper 在复制/替换成功后才清下载标记；这里立即清会失去
            # 失败时下次启动重试的机会。
            return True
    except Exception as exc:
        write_update_log(f'startup: apply downloaded update failed: {exc}')
        if attempts >= 3:
            clear_downloaded_update()
            _set_manual_update_notice(
                '更新安装连续失败 3 次，请手动下载新版或清理 SCL/update 后重试'
            )
    return False


def _resolve_update_exe(asset_path):
    '''返回 (可用的更新 exe, 是否为临时解压文件)。

    下载附件本身就是 exe 时直接使用，避免再次复制大文件；
    zip 附件才解压到 SCL/update 下。
    '''

    path = Path(asset_path)
    if not path.is_file():
        raise DownloadError('更新安装包不存在，请重新下载更新。')
    if path.suffix.lower() == '.exe':
        with path.open('rb') as handle:
            if handle.read(2) != b'MZ':
                raise DownloadError('下载的更新文件不是有效的 exe')
        return path, False
    return _extract_exe(path), True


def _wait_pid_exit(pid, timeout_ms):
    """等待指定 PID 退出；进程已不存在视为完成。"""

    if pid is None or int(pid) <= 0 or int(pid) == os.getpid():
        return True
    if os.name != 'nt':
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        while time.monotonic() < deadline:
            try:
                os.kill(int(pid), 0)
            except OSError:
                return True
            time.sleep(0.1)
        return False
    try:
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32,
        ]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        # SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(0x00100000, False, int(pid))
        if not handle:
            return True
        try:
            result = kernel32.WaitForSingleObject(handle, int(timeout_ms))
            # WAIT_OBJECT_0 = 0, WAIT_ABANDONED = 0x80
            return result in (0, 0x80)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return True


def _process_executable_path(pid):
    """返回指定 PID 的 exe 完整路径；拿不到时返回空字符串。"""

    if os.name != 'nt' or not pid or int(pid) <= 0:
        return ''
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = wintypes.BOOL
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            try:
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)
                ):
                    return str(buffer.value or '')
            finally:
                kernel32.CloseHandle(handle)
    except Exception:
        pass
    try:
        import psutil
        return str(psutil.Process(int(pid)).exe() or '')
    except Exception:
        return ''


def _same_executable_path(left, right):
    try:
        return bool(
            left and right
            and os.path.normcase(os.path.abspath(str(left)))
            == os.path.normcase(os.path.abspath(str(right)))
        )
    except (OSError, ValueError):
        return False


def _terminate_process(pid, allowed_path=None):
    """结束指定 PID；传入 allowed_path 时必须先确认路径一致。"""

    if not pid or int(pid) <= 0:
        return False
    if allowed_path:
        actual = _process_executable_path(int(pid))
        if not _same_executable_path(actual, allowed_path):
            write_update_log(
                f'kill skipped: pid={pid} path={actual!r} '
                f'allowed={allowed_path!r}'
            )
            return False
    if os.name == 'nt':
        try:
            subprocess.run(
                ['taskkill', '/F', '/PID', str(int(pid))],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                timeout=10,
            )
            return True
        except Exception as exc:
            write_update_log(f'kill failed: pid={pid}: {exc}')
    return False


def _wait_or_kill(pid, timeout_ms, label, allowed_path=None):
    if not pid or int(pid) <= 0:
        return True
    if _wait_pid_exit(int(pid), timeout_ms):
        write_update_log(f'{label} pid={pid} exited')
        return True
    write_update_log(f'{label} pid={pid} wait timeout; terminating')
    _terminate_process(int(pid), allowed_path)
    if _wait_pid_exit(int(pid), 5000):
        write_update_log(f'{label} pid={pid} terminated')
        return True
    write_update_log(f'{label} pid={pid} still alive after terminate')
    return False


def launch_update_helper(asset_path, version):
    """由旧进程拉起下载到的新 exe，让新 exe 作为 updater。

    旧进程不再改名/复制当前 exe；它只启动新 exe 并立即退出。新 exe 等待
    旧 PID 和 PyInstaller 父 PID 退出后完成 copy -> .new -> os.replace。
    """

    if not getattr(sys, 'frozen', False):
        raise DownloadError('源码运行模式不支持自动替换 exe，请手动下载更新。')
    target = Path(sys.executable).resolve()
    new_exe = _extract_exe(asset_path)
    if not new_exe.is_file() or new_exe.stat().st_size <= 0:
        raise DownloadError('下载的更新文件为空')
    try:
        with new_exe.open('rb') as handle:
            if handle.read(2) != b'MZ':
                raise DownloadError('下载的更新文件不是有效的 exe')
    except OSError as exc:
        raise DownloadError(f'无法读取更新文件：{exc}') from exc
    marker = _pending_update_marker(version)
    parent_pid = os.getppid()
    command = [
        str(new_exe), '--apply-update', str(target), str(version),
        str(os.getpid()), str(parent_pid),
    ]
    write_update_log(
        f'helper launch: target={target} new={new_exe} '
        f'old_pid={os.getpid()} parent_pid={parent_pid}'
    )
    try:
        subprocess.Popen(
            command,
            cwd=str(target.parent),
            close_fds=True,
            env=_clean_child_environment(),
            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
        )
    except OSError as exc:
        write_update_log(f'helper launch failed: {exc}')
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass
        raise DownloadError(f'无法启动更新程序：{exc}') from exc
    # 旧进程即使没有立即退出，也不能继续占用目标 exe；5 秒后强制退出。
    timer = threading.Timer(5.0, lambda: os._exit(0))
    timer.daemon = True
    timer.start()
    return new_exe


def run_update_helper(argv):
    """新 exe 的 updater 模式；返回进程退出码。"""

    if not getattr(sys, 'frozen', False):
        return 1
    try:
        index = list(argv).index('--apply-update')
        values = list(argv)[index + 1:index + 5]
        if len(values) < 4:
            raise ValueError('missing update arguments')
        target = Path(values[0]).resolve()
        version = str(values[1])
        old_pid = int(values[2])
        parent_pid = int(values[3])
    except Exception as exc:
        write_update_log(f'update helper: invalid args: {exc}')
        return 1
    source = Path(sys.executable).resolve()
    write_update_log(
        f'update helper: source={source} target={target} version={version} '
        f'old_pid={old_pid} parent_pid={parent_pid}'
    )
    try:
        if not _wait_or_kill(old_pid, 45000, 'old launcher', target):
            raise DownloadError('等待旧启动器退出超时，且无法结束旧进程')
        if parent_pid and parent_pid != old_pid:
            parent_path = _process_executable_path(parent_pid)
            if _same_executable_path(parent_path, target):
                if not _wait_or_kill(
                    parent_pid, 15000, 'launcher parent', target
                ):
                    raise DownloadError(
                        '等待 PyInstaller 父进程退出超时，且无法结束父进程'
                    )
            else:
                write_update_log(
                    f'update helper: skip parent kill pid={parent_pid} '
                    f'path={parent_path!r}'
                )
        if source != target:
            temp = target.with_name(target.name + '.new')
            last_error = None
            replaced = False
            for _ in range(120):
                try:
                    if temp.exists():
                        temp.unlink()
                    shutil.copy2(source, temp)
                    if temp.stat().st_size != source.stat().st_size:
                        raise OSError('copy size mismatch')
                    with temp.open('rb') as handle:
                        if handle.read(2) != b'MZ':
                            raise OSError('copy is not a PE file')
                    os.replace(temp, target)
                    replaced = True
                    break
                except OSError as exc:
                    last_error = exc
                    time.sleep(0.25)
            if not replaced:
                raise DownloadError(f'替换启动器失败：{last_error}')
        _updated_update_marker(version)
        write_update_log('update helper: replacement succeeded')
        try:
            _pending_update_path().unlink(missing_ok=True)
        except OSError:
            pass
        clear_downloaded_update()
        started = False
        try:
            subprocess.Popen(
                [str(target)],
                cwd=str(target.parent),
                close_fds=True,
                env=_clean_child_environment(),
                creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
            )
            started = True
        except OSError as exc:
            write_update_log(f'update helper: restart failed: {exc}')
        if not started:
            try:
                os.startfile(str(target))
            except OSError:
                pass
        write_update_log('update helper: updated and restarted')
        return 0
    except Exception as exc:
        write_update_log(f'update helper failed: {exc}')
        try:
            _failed_update_marker(version, str(exc))
            _pending_update_path().unlink(missing_ok=True)
        except OSError:
            pass
        # 目标 exe 仍是旧版，尽量把它重新启动，避免用户没有界面。
        try:
            subprocess.Popen(
                [str(target)],
                cwd=str(target.parent),
                close_fds=True,
                env=_clean_child_environment(),
                creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
            )
        except OSError:
            pass
        return 1


def apply_update_inplace(asset_path, version):
    """兼容旧调用：改为启动新 exe updater，不再直接改当前 exe。"""

    launch_update_helper(asset_path, version)
    return True


def _taskkill_old_image_names(paths):
    """psutil 不可用时按 exe 文件名结束旧备份进程。"""

    names = sorted({
        Path(str(path)).name for path in paths if Path(str(path)).name
    })
    for name in names:
        try:
            subprocess.run(
                ['taskkill', '/F', '/IM', name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                timeout=10,
            )
            write_update_log(f'taskkill fallback for old executable: {name}')
        except Exception as exc:
            write_update_log(
                f'taskkill fallback failed for {name}: '
                f'{type(exc).__name__}: {exc}'
            )


def _terminate_old_processes(paths):
    """结束仍从 .old 备份运行的旧启动器进程，避免文件一直被占用。"""

    if os.name != 'nt' or not paths:
        return
    resolved = []
    for path in paths:
        try:
            resolved.append(Path(str(path)).resolve())
        except OSError:
            resolved.append(Path(str(path)))
    try:
        import psutil
    except Exception:
        _taskkill_old_image_names(resolved)
        return
    targets = {
        os.path.normcase(os.path.abspath(str(path))) for path in resolved
    }
    for process in psutil.process_iter(['pid', 'exe']):
        try:
            pid = int(process.info.get('pid') or 0)
            exe = str(process.info.get('exe') or '')
            if not pid or pid == os.getpid() or not exe:
                continue
            if os.path.normcase(os.path.abspath(exe)) not in targets:
                continue
            subprocess.run(
                ['taskkill', '/F', '/PID', str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                timeout=10,
            )
            write_update_log(f'terminated stale old executable process: {pid}')
        except Exception:
            continue


def _schedule_delete_on_reboot(path):
    '''Windows 兜底：仍被句柄占用的 .old 放到重启时删除。'''

    if os.name != 'nt':
        return False
    try:
        import ctypes
        fn = ctypes.windll.kernel32.MoveFileExW
        fn.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        fn.restype = ctypes.c_bool
        # MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
        return bool(fn(str(path), None, 0x4))
    except Exception:
        return False


def cleanup_old_executable(retries=20, retry_rounds=1):
    '''启动后清理上一版重命名留下的 .old 文件。

    安全软件/旧进程可能在启动瞬间仍持有 exe 句柄；单轮失败就留下
    .old 是之前“更新后无法删除”的原因之一。这里按 0/1/2/4/8/15/
    30/60/120 秒多轮重试，每次启动也会重新尝试，文件释放后自动清掉。
    仍被占用时注册 Windows 重启删除，并返回残留路径供 UI 提示。
    '''

    if not getattr(sys, 'frozen', False):
        return []
    try:
        target = Path(sys.executable).resolve()
        old_internal = target.parent / '_internal.old'
        # 一次清理尝试最多等待约 5 秒；多轮之间给杀软/旧进程留出释放时间。
        for round_index in range(max(1, int(retry_rounds))):
            if round_index:
                time.sleep(
                    (1, 2, 4, 8, 15, 30, 60, 120)[
                        min(round_index - 1, 7)
                    ]
                )
            candidates = list(target.parent.glob(target.name + '*.old'))
            # 每一轮删 .old 前都先结束仍从该备份运行的进程；否则任务管理器/
            # 更新器留下的句柄会让后续多轮重试全部失败。
            if candidates:
                _terminate_old_processes(candidates)
            for old_exe in candidates:
                last_error = None
                for _ in range(max(1, int(retries))):
                    try:
                        if not old_exe.exists():
                            break
                        try:
                            os.chmod(old_exe, 0o666)
                        except OSError:
                            pass
                        old_exe.unlink()
                        write_update_log(f'cleaned old executable {old_exe.name}')
                        break
                    except OSError as exc:
                        last_error = exc
                        time.sleep(0.25)
                else:
                    write_update_log(
                        f'old executable still locked: {old_exe.name}: {last_error}'
                    )
            for _ in range(max(1, int(retries))):
                if not old_internal.exists():
                    break
                safe_rmtree(old_internal)
                if not old_internal.exists():
                    write_update_log('cleaned old _internal backup')
                    break
                time.sleep(0.25)
            if not list(target.parent.glob(target.name + '*.old')) and not old_internal.exists():
                return []
        remaining = [
            path for path in target.parent.glob(target.name + '*.old')
            if path.exists()
        ]
        _terminate_old_processes(remaining)
        for path in remaining:
            try:
                os.chmod(path, 0o666)
                path.unlink()
                write_update_log(f'cleaned old executable after taskkill {path.name}')
            except OSError:
                if _schedule_delete_on_reboot(path):
                    write_update_log(
                        f'scheduled old executable for reboot deletion: {path.name}'
                    )
        remaining = [path for path in remaining if path.exists()]
        return remaining
    except OSError:
        return []


def prepare_update(asset_path, version):
    '''下载完成后启动新 exe updater；调用方随后应退出启动器。'''

    if not getattr(sys, 'frozen', False):
        raise DownloadError('源码运行模式不支持自动替换 exe，请手动下载更新。')
    if is_onedir_install():
        return _prepare_onedir_update(asset_path, version)
    return launch_update_helper(asset_path, version)


def consume_failed_update_notice():
    """读取并清除更新失败回执，供启动器明确提示用户。"""

    marker = _update_marker_path(FAILED_MARKER)
    if not marker.is_file():
        return ''
    message = ''
    try:
        data = json.loads(marker.read_text(encoding='utf-8-sig'))
        reason = str(data.get('reason') or '').strip()
        version = str(data.get('version') or '').strip()
        message = '自动更新失败'
        if version:
            message += f'（{version}）'
        if reason:
            message += f'：{reason}'
    except (OSError, ValueError, TypeError):
        message = '自动更新失败，请手动下载最新版本'
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    return message


def consume_updated_marker():
    '''只消费安装脚本写下的成功回执；pending 是“正在安装”而非成功。'''

    marker = _updated_update_path()
    if not marker.is_file():
        return ''
    version = ''
    try:
        data = json.loads(marker.read_text(encoding='utf-8-sig'))
        if str(data.get('state') or 'updated') == 'updated':
            version = str(data.get('version') or '')
    except (OSError, ValueError):
        version = ''
    for path in (
        marker,
        _pending_update_path(),
        _update_marker_path(FAILED_MARKER),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return version


__all__ = [
    'CURRENT_VERSION', 'VERSION_DISPLAY', 'EXECUTABLE_BUILD', 'CheckUpdateWorker',
    'UpdateDownloadWorker', 'apply_downloaded_update_on_startup',
    'apply_update_inplace', 'cleanup_old_executable', 'clear_downloaded_update',
    'consume_updated_marker', 'downloaded_update_available',
    'pending_update_exists', 'clear_launcher_caches',
    'fetch_latest_release', 'is_newer', 'is_onedir_install', 'is_unpublished_build',
    'prepare_update', 'update_kind', 'write_downloaded_update', 'write_update_log',
    'consume_failed_update_notice', 'consume_manual_update_notice',
]
