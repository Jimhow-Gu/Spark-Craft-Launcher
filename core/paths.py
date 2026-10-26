'''启动器运行目录：所有运行数据统一放在 exe 同级的 SCL 文件夹。'''

from __future__ import annotations
import os
import stat
import shutil
import sys
from pathlib import Path
SCL_FOLDER_NAME = 'SCL'
RUNTIME_FOLDERS = ('log', 'background', 'music', 'temp_install', 'skins', 'icon_cache')


def _is_reparse_point(path):
    try:
        attrs = os.lstat(str(path)).st_file_attributes
    except (OSError, AttributeError):
        return os.path.islink(str(path))
    return bool(attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def safe_rmtree(path, ignore_errors=True):
    '''删除目录树，但绝不跟随 junction/symlink 删除目标目录内容。

    OptiFine 安装器会在临时目录里创建指向 .minecraft 的 junction；
    普通 shutil.rmtree 在某些环境下可能把游戏目录一起清空。
    '''

    path = Path(path)
    try:
        if path.is_symlink() or _is_reparse_point(path):
            try:
                os.rmdir(str(path))
            except OSError:
                try:
                    os.unlink(str(path))
                except OSError:
                    if not ignore_errors:
                        raise
            return
        if not path.is_dir():
            return
        for child in path.iterdir():
            if child.is_symlink() or _is_reparse_point(child):
                try:
                    os.rmdir(str(child))
                except OSError:
                    try:
                        os.unlink(str(child))
                    except OSError:
                        pass
            elif child.is_dir():
                safe_rmtree(child, ignore_errors)
            else:
                try:
                    child.unlink()
                except OSError:
                    if not ignore_errors:
                        raise
        try:
            path.rmdir()
        except OSError:
            if not ignore_errors:
                raise
    except OSError:
        if not ignore_errors:
            raise


def launcher_base_dir():
    '''启动器所在目录：打包后是 exe 同级，源码运行是项目根目录。'''

    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent

def scl_dir():
    path = launcher_base_dir() / SCL_FOLDER_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path

def _unique_dest(dest):
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    index = 2
    while True:
        candidate = dest.with_name(f'{stem} ({index}){suffix}')
        if not candidate.exists():
            return candidate
        index += 1

def _merge_move(src, dest):
    '''把 src 合并移动到 dest；同名内容保留双方，避免覆盖用户数据。'''

    if not src.exists():
        return
    if not dest.exists():
        try:
            shutil.move(str(src), str(dest))
            return
        except OSError:
            pass
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        try:
            children = list(src.iterdir())
        except OSError:
            children = []
        for child in children:
            target = dest / child.name
            if target.exists() and child.is_dir() and target.is_dir():
                _merge_move(child, target)
            else:
                _merge_move(child, _unique_dest(target) if target.exists() else target)
        try:
            src.rmdir()
        except OSError:
            return
    target = _unique_dest(dest) if dest.exists() else dest
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(target))
    except OSError:
        pass

def migrate_legacy_dirs():
    '''把旧版放在启动器根目录 / 用户配置目录的运行数据迁入 SCL。'''

    base = launcher_base_dir()
    runtime = scl_dir()
    for name in RUNTIME_FOLDERS:
        src = base / name
        dst = runtime / name
        if src.resolve() == dst.resolve():
            continue
        _merge_move(src, dst)
    try:
        config_dir = Path.home() / '.mc_launcher_py'
    except Exception:
        config_dir = None
    if config_dir and config_dir.is_dir():
        _merge_move(config_dir / 'skins', runtime / 'skins')
        _merge_move(config_dir / 'icon_cache', runtime / 'icon_cache')
        authlib = config_dir / 'authlib-injector.jar'
        if authlib.is_file():
            _merge_move(authlib, runtime / 'authlib-injector.jar')
        versions_cache = config_dir / 'mc_versions_cache.json'
        if versions_cache.is_file():
            cache_dir = runtime / 'cache'
            cache_dir.mkdir(parents=True, exist_ok=True)
            _merge_move(versions_cache, cache_dir / 'mc_versions_cache.json')

def ensure_runtime_dirs():
    root = scl_dir()
    for name in RUNTIME_FOLDERS:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / 'cache').mkdir(parents=True, exist_ok=True)

