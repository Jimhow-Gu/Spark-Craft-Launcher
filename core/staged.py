"""通用暂存下载：先写临时文件，校验通过后再原子替换目标。"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Optional

from core.net import (
    DownloadCancelled,
    DownloadError,
    HttpClient,
    _remove_part_files,
    hash_file,
)


class StagedDownloadError(DownloadError):
    pass


def staged_path(dest) -> Path:
    dest = Path(dest)
    return dest.with_name(dest.name + '.new')


def _remove_staged(temp: Path):
    try:
        temp.unlink(missing_ok=True)
    except OSError:
        pass
    _remove_part_files(temp.with_name(temp.name + '.part'))


def _verify_staged(path: Path, *, sha1='', sha256='', size=None, check_zip=False):
    try:
        if not path.is_file():
            return False, '临时文件不存在'
        actual_size = path.stat().st_size
        if size is not None and int(size) > 0 and actual_size != int(size):
            return False, f'大小不匹配（期望 {size}，实际 {actual_size}）'
        if sha1:
            actual = hash_file(path, 'sha1')
            if actual.lower() != str(sha1).lower():
                return False, f'SHA1 不匹配（期望 {sha1}，实际 {actual}）'
        if sha256:
            actual = hash_file(path, 'sha256')
            if actual.lower() != str(sha256).lower():
                return False, f'SHA256 不匹配（期望 {sha256}，实际 {actual}）'
        if check_zip:
            try:
                with zipfile.ZipFile(path) as zf:
                    if not zf.namelist():
                        return False, 'zip 结构为空'
            except (OSError, zipfile.BadZipFile):
                return False, 'zip 结构损坏'
        return True, ''
    except OSError as exc:
        return False, f'读取临时文件失败：{exc}'


def staged_download(
    candidates: Iterable,
    dest,
    *,
    http: Optional[HttpClient] = None,
    sha1: str = '',
    sha256: str = '',
    size=None,
    check_zip: bool = False,
    progress: Optional[Callable] = None,
    cancel: Optional[Callable] = None,
    status_cb: Optional[Callable] = None,
    headers: Optional[dict] = None,
    allow_slow_fallback: bool = False,
    log: Optional[Callable] = None,
    error_label: str = '文件下载',
):
    """依次尝试候选源，全部成功后原子替换 dest。

    失败时绝不删除旧 dest；目标被占用时保留完整临时文件并抛出可重试错误。
    """

    candidates = list(candidates or [])
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = staged_path(dest)
    owns_http = http is None
    http = http or HttpClient(timeout=60)
    errors = []
    try:
        if temp.exists():
            ok, reason = _verify_staged(
                temp, sha1=sha1, sha256=sha256, size=size, check_zip=check_zip
            )
            if ok:
                try:
                    os.replace(temp, dest)
                    return dest
                except OSError as exc:
                    raise StagedDownloadError(
                        f'目标文件被占用，已保留临时文件 {temp.name}：{exc}'
                    ) from exc
            _remove_staged(temp)
            if log:
                log(f'staged: discarded invalid temp {temp.name}: {reason}')

        for index, item in enumerate(candidates):
            if cancel and cancel():
                raise DownloadCancelled()
            if isinstance(item, (tuple, list)):
                url = str(item[0] if item else '')
                item_headers = dict(item[1] or {}) if len(item) > 1 else {}
            else:
                url = str(item or '')
                item_headers = dict(headers or {})
            if not url:
                continue
            if status_cb:
                status_cb(
                    '正在下载安装器...' if index == 0
                    else '下载源不可用，正在切换备用源...'
                )
            try:
                http.download(
                    url,
                    temp,
                    sha1=sha1 or None,
                    sha256=sha256 or None,
                    size=int(size) if size else None,
                    progress=progress,
                    cancel=cancel,
                    headers=item_headers or None,
                    allow_slow_fallback=allow_slow_fallback,
                )
                ok, reason = _verify_staged(
                    temp, sha1=sha1, sha256=sha256, size=size, check_zip=check_zip
                )
                if not ok:
                    _remove_staged(temp)
                    errors.append(f'{url}: {reason}')
                    if log:
                        log(f'staged: verify failed {url}: {reason}')
                    continue
                try:
                    os.replace(temp, dest)
                except OSError as exc:
                    raise StagedDownloadError(
                        f'目标文件被占用，已保留临时文件 {temp.name}：{exc}'
                    ) from exc
                if log:
                    log(f'staged: replaced {dest.name} from {url}')
                return dest
            except DownloadCancelled:
                _remove_staged(temp)
                raise
            except StagedDownloadError:
                raise
            except Exception as exc:
                errors.append(f'{url}: {exc}')
                if log:
                    log(
                        f'staged: candidate failed {url}: '
                        f'{type(exc).__name__}: {exc}'
                    )
                continue
        _remove_staged(temp)
        detail = '; '.join(errors[-3:]) if errors else '没有可用下载地址'
        raise StagedDownloadError(f'{error_label}失败：{detail}')
    finally:
        if owns_http:
            http.close()


__all__ = [
    'StagedDownloadError',
    'staged_download',
    'staged_path',
]
