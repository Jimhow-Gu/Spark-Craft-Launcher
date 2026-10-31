'''启动器版本检查与自动更新。'''

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.net import DownloadCancelled, DownloadError, HttpClient
from core.paths import safe_rmtree, scl_dir

CURRENT_VERSION = '1.0.8'
VERSION_DISPLAY = 'V1.0.8（正式版）'
# DLL 构建号在同一版本内递增；每次发布新大版本时重置为 1。
EXECUTABLE_BUILD = '20'
REPO = 'Jimhow-Gu/Spark-Craft-Launcher'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
API_ACCELERATED = f'https://gh-proxy.com/{API_LATEST}'
# 中国大陆可用的 GitHub 下载加速线路，按实测速度排序；最后始终回退官方源。
DOWNLOAD_ACCELERATORS = (
    'https://ghfast.top/',
    'https://gh-proxy.com/',
    'https://ghproxy.cc/',
    'https://ghproxy.net/',
)


def _download_urls(url):
    '''生成 GitHub 加速下载候选地址，保留官方源作为最后回退。'''

    url = str(url or '').strip()
    if not url:
        return []
    if not url.startswith(('https://github.com/', 'http://github.com/', 'https://objects.githubusercontent.com/')):
        return [url]
    candidates = [prefix + url for prefix in DOWNLOAD_ACCELERATORS]
    candidates.append(url)
    return list(dict.fromkeys(candidates))


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


def fetch_latest_release(timeout=12):
    '''读取最新 Release；直连失败时使用国内可访问的代理接口。'''

    http = HttpClient(timeout=timeout, retries=1, verify=False)
    data = None
    last_error = None
    for api_url in (API_LATEST, API_ACCELERATED):
        try:
            data = http.get_json(
                api_url,
                headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Spark-Craft-Launcher-Updater'},
            )
            if isinstance(data, dict):
                break
        except Exception as exc:
            data = None
            last_error = exc
    if not isinstance(data, dict):
        if last_error:
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
            self.done.emit(None, str(exc), self.manual)
            return
        self.done.emit(info, '', self.manual)


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
        url = str(self.info.get('asset_url') or '')
        name = str(self.info.get('asset_name') or 'Spark Craft Launcher-update.exe')
        if not url:
            self.failed.emit('该 Release 没有可下载的 exe/zip 附件')
            return
        update_dir = scl_dir() / 'update' / 'downloads'
        update_dir.mkdir(parents=True, exist_ok=True)
        dest = update_dir / re.sub(r'[^A-Za-z0-9._-]', '_', name)
        digest = str(self.info.get('asset_digest') or '')
        sha256 = digest[7:].strip() if digest.lower().startswith('sha256:') else None
        urls = _download_urls(url)
        last_error = None
        http = HttpClient(timeout=25, retries=1, verify=False)
        try:
            for index, candidate in enumerate(urls):
                if self._cancelled:
                    self.failed.emit('更新下载已取消')
                    return
                if index == 0:
                    self.status.emit('正在通过中国大陆加速线路下载更新...')
                elif index < len(urls) - 1:
                    self.status.emit(f'加速线路 {index} 不可用，正在切换线路...')
                else:
                    self.status.emit('加速线路暂不可用，正在从 GitHub 官方源下载...')
                try:
                    http.download(
                        candidate, dest,
                        sha256=sha256,
                        size=int(self.info.get('asset_size') or 0) or None,
                        progress=(lambda done, total: self.progress.emit(int(done), int(total))),
                        cancel=(lambda: self._cancelled),
                    )
                    try:
                        write_downloaded_update(self.info, dest)
                    except Exception:
                        pass
                    self.done.emit(str(dest))
                    return
                except DownloadCancelled:
                    self.failed.emit('更新下载已取消')
                    return
                except Exception as exc:
                    last_error = exc
                    continue
        finally:
            http.close()
        self.failed.emit(f'更新下载失败：{last_error or "所有线路均不可用"}')


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
        members = [name for name in zf.namelist() if Path(name).suffix.lower() == '.exe']
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
    'fetch_latest_release', 'is_newer', 'prepare_update', 'update_kind',
    'write_downloaded_update', 'write_update_log',
]
