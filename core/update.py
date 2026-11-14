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

from core.net import DownloadCancelled, DownloadError, HttpClient
from core.paths import safe_rmtree, scl_dir

CURRENT_VERSION = '1.11.4'
VERSION_DISPLAY = 'V1.11.4（正式版）'
# DLL 构建号在同一版本内递增；每次发布新大版本时重置为 1。
EXECUTABLE_BUILD = '26'
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


def _fetch_release_api(api_url, timeout, results):
    http = None
    try:
        http = HttpClient(timeout=timeout, retries=1)
        data = http.get_json(
            api_url,
            headers={
                'Accept': 'application/vnd.github+json',
                'User-Agent': 'Spark-Craft-Launcher-Updater',
            },
        )
        if isinstance(data, dict) and data.get('tag_name'):
            results.put((api_url, data, None))
            return
        results.put((api_url, None, DownloadError('更新接口没有返回有效的 Release 信息')))
    except Exception as exc:
        results.put((api_url, None, exc))
    finally:
        if http is not None:
            http.close()


def fetch_latest_release(timeout=12):
    '''并发读取最新 Release；哪个官方接口或镜像先返回就用哪个。'''

    sources = list(dict.fromkeys(API_SOURCES))
    results = queue.Queue()
    for api_url in sources:
        threading.Thread(
            target=_fetch_release_api,
            args=(api_url, timeout, results),
            daemon=True,
            name='SCL-UpdateMeta',
        ).start()

    data = None
    last_error = None
    deadline = time.monotonic() + max(6.0, float(timeout) + 2.0)
    pending = len(sources)
    while pending > 0:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            _api_url, payload, error = results.get(timeout=remaining)
        except queue.Empty:
            break
        pending -= 1
        if isinstance(payload, dict) and payload.get('tag_name'):
            data = payload
            break
        if error is not None:
            last_error = error
    if not isinstance(data, dict):
        if last_error is not None:
            raise last_error
        return None

    assets = data.get('assets') or []
    chosen = None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get('name') or '').lower()
        if name.endswith('.exe'):
            chosen = asset
            break
    if chosen is None:
        for asset in assets:
            if isinstance(asset, dict) and str(asset.get('name') or '').lower().endswith('.zip'):
                chosen = asset
                break
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
                    try:
                        version = str(self.info.get('version') or self.info.get('tag') or '')
                        prepare_update(dest, version)
                        threading.Timer(2.0, lambda: os._exit(0)).start()
                        write_update_log('download: external updater armed; exiting in 2s')
                    except Exception as exc:
                        write_update_log(f'download: external updater failed: {exc}')
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


def _extract_exe(asset_path):
    path = Path(asset_path)
    target = scl_dir() / 'update' / 'Spark Craft Launcher.exe.new'
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == '.exe':
        data = path.read_bytes()[:2]
        if data != b'MZ':
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
    if target.read_bytes()[:2] != b'MZ':
        raise DownloadError('更新文件不是有效的 exe')
    return target


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


def _pending_update_marker(version):
    marker = scl_dir() / 'update' / 'pending_update.json'
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({'version': str(version), 'time': time.time()}, ensure_ascii=False),
        encoding='utf-8',
    )
    return marker


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
    return scl_dir() / 'update' / 'pending_update.json'


def pending_update_exists():
    return _pending_update_path().is_file()


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


def write_downloaded_update(info, asset_path):
    '''记录已下载完成的更新，供下次启动兜底自动安装。'''

    info = dict(info or {})
    path = Path(asset_path)
    if not path.is_file():
        return
    marker = _downloaded_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({
            'version': str(info.get('version') or info.get('tag') or '').lstrip('vV'),
            'tag': str(info.get('tag') or ''),
            'dll_build': int(info.get('dll_build') or 0),
            'asset_path': str(path),
            'asset_size': path.stat().st_size,
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
        path = Path(str(data.get('asset_path') or ''))
        if not path.is_file():
            return None
        expected = int(data.get('asset_size') or 0)
        if expected and path.stat().st_size != expected:
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
    asset = Path(str(data.get('asset_path') or ''))
    version = str(data.get('version') or data.get('tag') or '')
    write_update_log(f'startup: applying downloaded update {version} from {asset}')
    try:
        if apply_update_inplace(asset, version):
            clear_downloaded_update()
            return True
    except Exception as exc:
        write_update_log(f'startup: apply downloaded update failed: {exc}')
        clear_downloaded_update()
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


def apply_update_inplace(asset_path, version):
    '''在当前进程内完成替换并启动新 exe。

    Windows 允许重命名正在运行的 exe，但不允许直接覆盖。先把旧 exe
    改名为 .old，再把新文件复制到原路径，最后启动新进程。整个过程不依赖
    PowerShell，避免旧更新脚本在部分环境下未执行导致“更新后不重启”。
    '''

    if not getattr(sys, 'frozen', False):
        raise DownloadError('源码运行模式不支持自动替换 exe，请手动下载更新。')
    target = Path(sys.executable).resolve()
    if not target.is_file():
        raise DownloadError('找不到当前启动器 exe，无法自动更新。')
    write_update_log(
        f'inplace: start target={target} asset={asset_path} version={version}'
    )
    old_exe = target.with_name(target.name + '.old')
    moved = False
    extracted = False
    try:
        new_exe, extracted = _resolve_update_exe(asset_path)
        write_update_log(f'inplace: resolved new={new_exe}')
        if new_exe.resolve() == target:
            raise DownloadError('更新包与当前启动器相同，无需更新。')
        if old_exe.exists():
            try:
                old_exe.unlink()
            except OSError:
                # 上一次残留的 .old 仍被占用时换一个名字，避免本次更新失败。
                old_exe = target.with_name(
                    f'{target.name}.{os.getpid()}.old'
                )
        last_error = None
        for attempt in range(12):
            try:
                os.replace(target, old_exe)
                moved = True
                break
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        if not moved:
            raise DownloadError(f'无法重命名当前 exe：{last_error}')
        shutil.copy2(new_exe, target)
        # 更新只替换本体 exe；SCL 目录中的日志、皮肤、缓存和用户数据保持原位。
        write_update_log('inplace: exe replaced, SCL preserved')
        with target.open('rb') as handle:
            magic = handle.read(2)
        if magic != b'MZ' or target.stat().st_size != new_exe.stat().st_size:
            raise DownloadError('更新文件写入不完整')
        _pending_update_marker(version)
        if extracted:
            try:
                new_exe.unlink()
            except OSError:
                pass
        write_update_log('inplace: copy ok, launching new exe')
        last_launch_error = None
        for attempt in range(10):
            try:
                subprocess.Popen(
                    [str(target)],
                    cwd=str(target.parent),
                    close_fds=True,
                    env=_clean_child_environment(),
                    creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
                )
                last_launch_error = None
                break
            except OSError as exc:
                last_launch_error = exc
                time.sleep(0.3)
        if last_launch_error is not None:
            # Popen 失败时再走 ShellExecute，部分安全软件只拦截前者。
            try:
                saved = {}
                for key in list(os.environ):
                    upper = key.upper()
                    if (
                        upper.startswith('_PYI_')
                        or upper.startswith('_MEIPASS')
                        or upper.startswith('PYINSTALLER_')
                    ):
                        saved[key] = os.environ.pop(key)
                try:
                    os.startfile(str(target))
                finally:
                    os.environ.update(saved)
                last_launch_error = None
                write_update_log('inplace: started via ShellExecute')
            except OSError as exc:
                last_launch_error = exc
        if last_launch_error is not None:
            raise DownloadError(f'无法启动新版本：{last_launch_error}')
        write_update_log('inplace: restarted')
        clear_downloaded_update()
        return True
    except Exception as exc:
        write_update_log(f'inplace failed: {exc}')
        if moved:
            try:
                if target.exists():
                    target.unlink()
                os.replace(old_exe, target)
                write_update_log('inplace: restored previous exe')
            except OSError as restore_error:
                write_update_log(f'inplace restore failed: {restore_error}')
            try:
                (scl_dir() / 'update' / 'pending_update.json').unlink(missing_ok=True)
            except OSError:
                pass
        raise


def cleanup_old_executable(retries=20):
    '''启动成功后清理上一版重命名留下的 .old 文件。'''

    if not getattr(sys, 'frozen', False):
        return
    try:
        target = Path(sys.executable).resolve()
        candidates = list(target.parent.glob(target.name + '*.old'))
        if not candidates:
            return
        for old_exe in candidates:
            for _ in range(max(1, int(retries))):
                try:
                    old_exe.unlink()
                    write_update_log(f'cleaned old executable {old_exe.name}')
                    break
                except OSError:
                    time.sleep(0.25)
    except OSError:
        pass


def prepare_update(asset_path, version):
    '''下载完成后启动外部更新脚本；调用方随后应退出启动器。返回更新脚本路径。'''

    if not getattr(sys, 'frozen', False):
        raise DownloadError('源码运行模式不支持自动替换 exe，请手动下载更新。')
    target = Path(sys.executable).resolve()
    new_exe = _extract_exe(asset_path)
    update_dir = scl_dir() / 'update'
    marker = _pending_update_marker(version)
    script = update_dir / 'apply_update.ps1'
    script_text = chr(10).join([
        'param([int]$WaitPid,[string]$NewExe,[string]$Target,[string]$WorkDir)',
        '$LogDir = Join-Path $WorkDir "SCL\\update"',
        'New-Item -ItemType Directory -Force -Path $LogDir | Out-Null',
        '$Log = Join-Path $LogDir "apply_update.log"',
        'function Write-UpdaterLog([string]$Text) {',
        '    Add-Content -LiteralPath $Log -Value ("[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $Text) -Encoding UTF8',
        '}',
        'Write-UpdaterLog "waiting for pid $WaitPid"',
        'Wait-Process -Id $WaitPid -ErrorAction SilentlyContinue',
        'Start-Sleep -Milliseconds 800',
        '$copied = $false',
        'for ($i = 0; $i -lt 60; $i++) {',
        '    try {',
        '        Unblock-File -LiteralPath $NewExe -ErrorAction SilentlyContinue',
        '        Copy-Item -LiteralPath $NewExe -Destination $Target -Force -ErrorAction Stop',
        '        $copied = $true',
        '        Write-UpdaterLog "copy ok"',
        '        break',
        '    } catch {',
        '        if ($i -eq 0 -or ($i % 10) -eq 0) { Write-UpdaterLog ("copy retry: " + $_.Exception.Message) }',
        '        Start-Sleep -Milliseconds 500',
        '    }',
        '}',
        'if ($copied) {',
        '    Start-Sleep -Milliseconds 800',
        '    try {',
        '        Start-Process -FilePath $Target -WorkingDirectory $WorkDir -ErrorAction Stop',
        '        Write-UpdaterLog "restarted"',
        '    } catch {',
        '        Write-UpdaterLog ("restart failed: " + $_.Exception.Message)',
        '    }',
        '    Remove-Item -LiteralPath $NewExe -Force -ErrorAction SilentlyContinue',
        '} else {',
        '    Write-UpdaterLog "copy failed after retries"',
        '}',
    ]) + chr(10)
    script.write_text(script_text, encoding='utf-8-sig')
    write_update_log(f'prepare: target={target} new={new_exe} script={script}')
    try:
        subprocess.Popen(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
             str(os.getpid()), str(new_exe), str(target), str(target.parent)],
            creationflags=(
                getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            ),
            close_fds=True,
            cwd=str(target.parent),
            env=_clean_child_environment(),
        )
        write_update_log('prepare: powershell started')
    except OSError as exc:
        write_update_log(f'prepare: powershell failed: {exc}')
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return script


def consume_updated_marker():
    marker = scl_dir() / 'update' / 'pending_update.json'
    if not marker.is_file():
        return ''
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
        version = str(data.get('version') or '')
    except (OSError, ValueError):
        version = ''
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    return version


__all__ = [
    'CURRENT_VERSION', 'VERSION_DISPLAY', 'EXECUTABLE_BUILD', 'CheckUpdateWorker',
    'UpdateDownloadWorker', 'apply_downloaded_update_on_startup',
    'apply_update_inplace', 'cleanup_old_executable', 'clear_downloaded_update',
    'consume_updated_marker', 'downloaded_update_available',
    'pending_update_exists', 'clear_launcher_caches',
    'fetch_latest_release', 'is_newer', 'prepare_update', 'update_kind',
    'write_downloaded_update', 'write_update_log',
]
