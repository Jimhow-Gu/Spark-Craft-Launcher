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

CURRENT_VERSION = '1.0.0'
VERSION_DISPLAY = 'V1.0.0（正式版）'
REPO = 'Jimhow-GU/SparkCraftLauncher'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'


def _version_tuple(text):
    numbers = [int(part) for part in re.findall(r'\d+', str(text or ''))]
    return tuple(numbers[:4])


def is_newer(latest_tag, current=CURRENT_VERSION):
    latest = _version_tuple(latest_tag)
    current_tuple = _version_tuple(current)
    length = max(len(latest), len(current_tuple))
    return latest + (0,) * (length - len(latest)) > current_tuple + (0,) * (length - len(current_tuple))


def fetch_latest_release(timeout=12):
    http = HttpClient(timeout=timeout, retries=1)
    data = http.get_json(
        API_LATEST,
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'SparkCraftLauncher-Updater'},
    )
    if not isinstance(data, dict):
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
    return {
        'tag': tag,
        'version': tag.lstrip('vV'),
        'name': str(data.get('name') or tag),
        'body': str(data.get('body') or ''),
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
        name = str(self.info.get('asset_name') or 'SparkCraftLauncher-update.exe')
        if not url:
            self.failed.emit('该 Release 没有可下载的 exe/zip 附件')
            return
        update_dir = scl_dir() / 'update' / 'downloads'
        update_dir.mkdir(parents=True, exist_ok=True)
        dest = update_dir / re.sub(r'[^A-Za-z0-9._-]', '_', name)
        digest = str(self.info.get('asset_digest') or '')
        sha256 = digest[7:].strip() if digest.lower().startswith('sha256:') else None
        self.status.emit(f'正在下载 {self.info.get("tag") or "新版本"}...')
        try:
            http = HttpClient(timeout=30, retries=2)
            http.download(
                url, dest,
                sha256=sha256,
                size=int(self.info.get('asset_size') or 0) or None,
                progress=(lambda done, total: self.progress.emit(int(done), int(total))),
                cancel=(lambda: self._cancelled),
            )
        except DownloadCancelled:
            self.failed.emit('更新下载已取消')
            return
        except Exception as exc:
            self.failed.emit(f'更新下载失败：{exc}')
            return
        self.done.emit(str(dest))


def _extract_exe(asset_path):
    path = Path(asset_path)
    if path.suffix.lower() == '.exe':
        data = path.read_bytes()[:2]
        if data != b'MZ':
            raise DownloadError('下载的更新文件不是有效的 exe')
        target = scl_dir() / 'update' / 'SparkCraftLauncher.exe.new'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target
    if path.suffix.lower() != '.zip':
        raise DownloadError('不支持的更新附件格式')
    target = scl_dir() / 'update' / 'SparkCraftLauncher.exe.new'
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        member = next((name for name in zf.namelist() if Path(name).name.lower() == 'sparkcraftlauncher.exe'), '')
        if not member:
            raise DownloadError('更新 zip 中没有 SparkCraftLauncher.exe')
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
    script_text = (
        'param([int]$WaitPid,[string]$NewExe,[string]$Target,[string]$WorkDir)\n'
        'Wait-Process -Id $WaitPid -ErrorAction SilentlyContinue\n'
        'Start-Sleep -Milliseconds 600\n'
        'Copy-Item -LiteralPath $NewExe -Destination $Target -Force\n'
        'Start-Process -FilePath $Target -WorkingDirectory $WorkDir\n'
        'Remove-Item -LiteralPath $NewExe -Force -ErrorAction SilentlyContinue\n'
    )
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
    'CURRENT_VERSION', 'VERSION_DISPLAY', 'CheckUpdateWorker', 'UpdateDownloadWorker',
    'consume_updated_marker', 'fetch_latest_release', 'is_newer', 'prepare_update',
]
