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
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urljoin

from PySide6.QtCore import QThread, Signal

from core.net import DownloadCancelled, DownloadError, HttpClient
from core.paths import safe_rmtree, scl_dir
from core.staged import staged_download

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
            candidates.append((f'Temurin {feature}（清华镜像）', url, name, '', 0))
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
                    checksum = str(package.get('checksum') or '')
                    try:
                        package_size = int(package.get('size') or 0)
                    except (TypeError, ValueError):
                        package_size = 0
                    candidates.append((f'Temurin {feature}', link, name, checksum, package_size))
                    break
    except Exception:
        pass
    huawei = HUAWEI_OPENJDK.get(feature)
    if huawei:
        candidates.append((f'OpenJDK {feature}（华为镜像）', huawei, huawei.rsplit('/', 1)[-1], '', 0))
    microsoft = MICROSOFT_JDK.get(feature)
    if microsoft:
        candidates.append((f'Microsoft OpenJDK {feature}', microsoft, f'microsoft-jdk-{feature}-windows-x64.zip', '', 0))
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


def _runtime_in_use(path):
    """检查是否有进程正在使用该 Java 目录，避免误删正在运行的游戏 runtime。"""

    try:
        import psutil
    except Exception:
        return True
    root = os.path.normcase(os.path.abspath(str(path)))
    try:
        for proc in psutil.process_iter(['exe']):
            exe = str((proc.info or {}).get('exe') or '')
            if not exe:
                continue
            try:
                target = os.path.normcase(os.path.abspath(exe))
                if os.path.commonpath([root, target]) == root:
                    return True
            except (OSError, ValueError):
                continue
    except Exception:
        return True
    return False


def cleanup_old_java_runtimes(runtime_root=None):
    """清理上次 Java 原子切换留下的 .old-* 目录；正在使用时跳过。"""

    root = Path(runtime_root or (scl_dir() / 'runtime'))
    if not root.is_dir():
        return
    for old in root.glob('.Java-*.old-*'):
        if _runtime_in_use(old):
            continue
        safe_rmtree(old, ignore_errors=True)


def _cleanup_old_runtimes(runtime_root):
    cleanup_old_java_runtimes(runtime_root)


def _replace_runtime(target, staging):
    """把解压好的新 runtime 原子切换进去，失败恢复旧目录。"""

    target = Path(target)
    staging = Path(staging)
    token = uuid.uuid4().hex[:8]
    backup = target.parent / f'.{target.name}.old-{token}'
    had_old = target.exists()
    if had_old and _runtime_in_use(target):
        raise DownloadError(
            '请先关闭正在使用该 Java 的游戏，再更新 Java 运行时'
        )
    if had_old:
        try:
            target.rename(backup)
        except OSError as exc:
            raise DownloadError(
                '无法替换 Java 运行时，请先关闭正在使用该 Java 的游戏后重试'
            ) from exc
    try:
        staging.rename(target)
    except OSError as exc:
        if had_old and backup.exists() and not target.exists():
            try:
                backup.rename(target)
            except OSError:
                pass
        raise DownloadError(f'切换 Java 运行时失败：{exc}') from exc
    return target


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
    _cleanup_old_runtimes(runtime_root)
    errors = []
    for index, candidate in enumerate(candidates, 1):
        label, url, name = candidate[0], candidate[1], candidate[2]
        checksum = str(candidate[3] if len(candidate) > 3 else '')
        expected_size = int(candidate[4] if len(candidate) > 4 else 0) or None
        if cancel():
            raise DownloadCancelled()
        zip_path = downloads / name
        status(f'正在下载 {label}...')
        staging = target.parent / f'.{target.name}.new-{uuid.uuid4().hex[:8]}'
        try:
            staged_download(
                [url],
                zip_path,
                http=http,
                sha256=checksum or '',
                size=expected_size,
                check_zip=True,
                progress=progress,
                cancel=cancel,
                status_cb=status,
                error_label='Java 压缩包下载',
            )
            status('正在解压 Java...')
            _extract_zip(zip_path, staging)
            exe = staging / 'bin' / 'javaw.exe'
            if not exe.is_file():
                exe = staging / 'bin' / 'java.exe'
            if not exe.is_file() or not (staging / 'release').is_file():
                raise DownloadError('Java 压缩包结构不完整（缺少 bin/java 或 release）')
            major = java_major_of(exe)
            if major is None:
                raise DownloadError('下载的 Java 无法运行')
            if major != feature:
                raise DownloadError(f'下载的 Java 版本为 {major}，与需要的 {feature} 不一致')
            _replace_runtime(target, staging)
            status(f'Java {feature} 安装完成')
            return target / 'bin' / ('javaw.exe' if (target / 'bin' / 'javaw.exe').is_file() else 'java.exe')
        except DownloadCancelled:
            safe_rmtree(staging, ignore_errors=True)
            raise
        except Exception as exc:
            errors.append(f'{label}: {exc}')
            safe_rmtree(staging, ignore_errors=True)
            continue
    raise DownloadError(
        f'Java {feature} 下载失败：{chr(10)}' + chr(10).join(errors[-4:])
    )


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


__all__ = ['JavaDownloadWorker', 'cleanup_old_java_runtimes', 'install_java_runtime', 'java_major_of']
