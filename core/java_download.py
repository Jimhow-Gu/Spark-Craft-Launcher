'''
启动器内置 Java 运行时下载（参考 HMCL 的 JavaDistribution/JavaInstallTask 思路独立实现）。

默认从清华 TUNA 的 Adoptium 镜像下载对应大版本的 JRE，解压到 SCL/runtime，
交给 Java 扫描/自动选择逻辑使用；下载失败的候选会逐个回退。
'''

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import urljoin

from PySide6.QtCore import QThread, Signal

from core.downloading import DownloadClient, RemoteFile
from core.net import DownloadCancelled, DownloadError, HttpClient
from core.paths import safe_rmtree, scl_dir

TUNA_ADOPTIUM = 'https://mirrors.tuna.tsinghua.edu.cn/Adoptium/{feature}/jre/x64/windows/'
ADOPTIUM_API = 'https://api.adoptium.net/v3/assets/latest/{feature}/hotspot?architecture=x64&image_type=jre&os=windows&vendor=eclipse'
HUAWEI_OPENJDK = {
    17: 'https://mirrors.huaweicloud.com/openjdk/17.0.2/openjdk-17.0.2_windows-x64_bin.zip',
    21: 'https://mirrors.huaweicloud.com/openjdk/21.0.2/openjdk-21.0.2_windows-x64_bin.zip',
}
MICROSOFT_JDK = {
    17: 'https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip',
    21: 'https://aka.ms/download-jdk/microsoft-jdk-21-windows-x64.zip',
}


def _natural_key(text):
    return [(0, int(part)) if part.isdigit() else (1, part.lower()) for part in re.split(r'(\d+)', str(text))]


def _major_from_version_text(text):
    match = re.search(r'version\s+"?((?:\d+))(?:\.(\d+))?', str(text or ''))
    if not match:
        return None
    major = int(match.group(1))
    if major == 1 and match.group(2):
        return int(match.group(2))
    return major


def java_major_of(exe_path):
    exe = Path(exe_path)
    java = exe.with_name('java.exe') if exe.name.lower() == 'javaw.exe' else exe
    if not java.is_file():
        java = exe
    try:
        proc = subprocess.run(
            [str(java), '-version'], capture_output=True, timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stderr or b'') + (proc.stdout or b'')
    return _major_from_version_text(text.decode('utf-8', 'replace'))


def _find_installed(feature):
    root = scl_dir() / 'runtime'
    if not root.is_dir():
        return None
    for exe in sorted(root.rglob('javaw.exe')):
        if java_major_of(exe) == int(feature):
            return exe
    return None


def _discover_candidates(feature, http):
    feature = int(feature)
    if feature == 16:
        feature = 17
    candidates = []
    page = TUNA_ADOPTIUM.format(feature=feature)
    try:
        response = http.get(page, timeout=15)
        links = re.findall(r'href="([^"]+\.zip)"', response.text or '', re.I)
        if links:
            names = []
            for link in links:
                name = link.rsplit('/', 1)[-1]
                names.append((name, urljoin(page, link)))
            names.sort(key=(lambda item: _natural_key(item[0])))
            name, url = names[-1]
            candidates.append((f'Temurin {feature}（清华镜像）', url, name))
    except Exception:
        pass
    try:
        rows = http.get_json(ADOPTIUM_API.format(feature=feature), timeout=15)
        if isinstance(rows, list):
            for row in rows:
                package = ((row or {}).get('binary') or {}).get('package') or {}
                link = str(package.get('link') or '')
                name = str(package.get('name') or '')
                if link and name:
                    candidates.append((f'Temurin {feature}', link, name))
                    break
    except Exception:
        pass
    huawei = HUAWEI_OPENJDK.get(feature)
    if huawei:
        candidates.append((f'OpenJDK {feature}（华为镜像）', huawei, huawei.rsplit('/', 1)[-1]))
    microsoft = MICROSOFT_JDK.get(feature)
    if microsoft:
        candidates.append((f'Microsoft OpenJDK {feature}', microsoft, f'microsoft-jdk-{feature}-windows-x64.zip'))
    return candidates


def _extract_zip(zip_path, dest):
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for item in zf.infolist():
            name = item.filename.replace('\\', '/')
            parts = [part for part in name.split('/') if part not in ('', '.')]
            if not parts or '..' in parts or (parts and ':' in parts[0]):
                continue
            target = dest.joinpath(*parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(item) as src, open(target, 'wb') as out:
                shutil.copyfileobj(src, out)


def install_java_runtime(feature, *, progress=None, status=None, cancel=None):
    '''下载并安装指定主版本的 Java，返回 javaw.exe 路径。'''

    feature = int(feature)
    installed = _find_installed(feature)
    if installed is not None:
        return installed
    status = status or (lambda _s: None)
    cancel = cancel or (lambda: False)
    runtime_root = scl_dir() / 'runtime'
    runtime_root.mkdir(parents=True, exist_ok=True)
    http = HttpClient(timeout=20, retries=2)
    status(f'正在查找 Java {feature} 下载地址...')
    candidates = _discover_candidates(feature, http)
    if not candidates:
        raise DownloadError(f'无法获取 Java {feature} 的下载地址，请检查网络后重试（或手动安装 Java {feature}）。')
    target = runtime_root / f'Java-{feature}'
    downloads = runtime_root / 'downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    errors = []
    for index, (label, url, name) in enumerate(candidates, 1):
        if cancel():
            raise DownloadCancelled()
        zip_path = downloads / name
        status(f'正在下载 {label}...')
        try:
            remote = RemoteFile(
                label=label, dest=zip_path, urls=[url],
                category='java', check_zip=True,
            )
            client = DownloadClient(runtime_root, fast=False, file_deadline=900)
            try:
                client.download(remote, cancel=cancel, progress=progress)
            finally:
                client.close()
            status('正在解压 Java...')
            safe_rmtree(target)
            _extract_zip(zip_path, target)
            exe = next((path for path in sorted(target.rglob('javaw.exe')) if path.is_file()), None)
            if exe is None:
                raise DownloadError('压缩包中没有找到 javaw.exe')
            major = java_major_of(exe)
            if major is None:
                raise DownloadError('下载的 Java 无法运行')
            if major != feature:
                raise DownloadError(f'下载的 Java 版本为 {major}，与需要的 {feature} 不一致')
            status(f'Java {feature} 安装完成')
            return exe
        except DownloadCancelled:
            raise
        except Exception as exc:
            errors.append(f'{label}: {exc}')
            try:
                zip_path.unlink(missing_ok=True)
            except OSError:
                pass
            safe_rmtree(target)
            continue
    raise DownloadError(f'Java {feature} 下载失败：\n' + '\n'.join(errors[-4:]))


class JavaDownloadWorker(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, feature, parent=None):
        super().__init__(parent)
        self.feature = int(feature)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            path = install_java_runtime(
                self.feature,
                progress=(lambda done, total: self.progress.emit(int(done), int(total))),
                status=self.status.emit,
                cancel=(lambda: self._cancelled),
            )
        except DownloadCancelled:
            self.failed.emit('下载已取消')
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(str(path))


__all__ = ['JavaDownloadWorker', 'install_java_runtime', 'java_major_of']
