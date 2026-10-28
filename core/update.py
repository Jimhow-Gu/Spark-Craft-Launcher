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
EXECUTABLE_BUILD = '8'
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


def prepare_update(asset_path, version):
    '''下载完成后启动外部更新脚本；调用方随后应退出启动器。返回更新脚本路径。'''

    if not getattr(sys, 'frozen', False):
        raise DownloadError('源码运行模式不支持自动替换 exe，请手动下载更新。')
    target = Path(sys.executable).resolve()
    new_exe = _extract_exe(asset_path)
    update_dir = scl_dir() / 'update'
    marker = update_dir / 'pending_update.json'
    marker.write_text(json.dumps({'version': str(version), 'time': time.time()}, ensure_ascii=False), encoding='utf-8')
    script = update_dir / 'apply_update.ps1'
    script_text = chr(10).join([
        'param([int]$WaitPid,[string]$NewExe,[string]$Target,[string]$WorkDir)',
        'Wait-Process -Id $WaitPid -ErrorAction SilentlyContinue',
        'Start-Sleep -Milliseconds 600',
        '$copied = $false',
        'for ($i = 0; $i -lt 30; $i++) {',
        '    try { Copy-Item -LiteralPath $NewExe -Destination $Target -Force -ErrorAction Stop; $copied = $true; break }',
        '    catch { Start-Sleep -Milliseconds 500 }',
        '}',
        'if ($copied) {',
        '    Start-Process -FilePath $Target -WorkingDirectory $WorkDir',
        '    Remove-Item -LiteralPath $NewExe -Force -ErrorAction SilentlyContinue',
        '}',
    ]) + chr(10)
    script.write_text(script_text, encoding='utf-8-sig')
    subprocess.Popen(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
         str(os.getpid()), str(new_exe), str(target), str(target.parent)],
        creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
        close_fds=True,
    )
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
    'UpdateDownloadWorker', 'consume_updated_marker', 'fetch_latest_release',
    'is_newer', 'update_kind', 'prepare_update',
]
