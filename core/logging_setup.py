'''统一日志目录与日志清理。

所有启动器日志、安装器日志和游戏输出日志统一写入启动器同级 SCL/log/。
'''

from __future__ import annotations
import logging
import shutil
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Tuple
from core.paths import launcher_base_dir, scl_dir
PROJECT_ROOT = launcher_base_dir()
LOG_DIR = scl_dir() / 'log'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / 'launcher.log'

def migrate_installer_logs():
    '''把启动器根目录/临时目录残留的日志统一迁移到 SCL/log/。'''

    sources = [PROJECT_ROOT, scl_dir() / 'temp_install']
    for source in sources:
        if not source.is_dir():
            continue
        try:
            files = list(source.glob('*.log'))
        except OSError:
            files = []
        for path in files:
            if not path.is_file():
                continue
            dest = LOG_DIR / path.name
            if dest.exists():
                dest = LOG_DIR / f'{path.stem}-{int(time.time())}{path.suffix}'
            try:
                shutil.move(str(path), str(dest))
            except OSError:
                pass

migrate_installer_logs()
_FORMAT = '[%(asctime)s] [%(levelname)s] %(name)s: %(message)s'
_DATEFMT = '%Y-%m-%d %H:%M:%S'

def _is_log_handler(handler):
    filename = getattr(handler, 'baseFilename', '') or ''
    if not filename:
        return False
    try:
        return Path(filename).resolve().parent == LOG_DIR.resolve()
    except Exception:
        return False

def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if sys.stderr is not None and not any(
        isinstance(h, logging.StreamHandler) for h in root.handlers
    ):
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(stream)
    if any((_is_log_handler(h) for h in root.handlers)):
        return
    try:
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5242880, backupCount=3, encoding='utf-8')
    except OSError:
        return
    file_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

def clear_logs():
    '''清空 log/ 下的所有日志，返回 (删除数量, 错误列表)。'''

    root = logging.getLogger()
    removed = 0
    errors = []
    for handler in list(root.handlers):
        if _is_log_handler(handler):
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
            root.removeHandler(handler)
    try:
        files = [p for p in LOG_DIR.rglob('*') if p.is_file()]
    except OSError as exc:
        files = []
        errors.append(str(exc))
    for path in files:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            errors.append(f'{path.name}: {exc}')
            continue
    setup_logging()
    logging.getLogger('Log').info('日志已清空（删除 %s 个文件）', removed)
    return removed, errors

