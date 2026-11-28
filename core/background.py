'''启动器背景图片设置（统一存放在 SCL/background）。'''

import json
import os
import random
import time
from pathlib import Path
from typing import List
from core.config import ConfigManager
from core.paths import scl_dir
IMAGE_EXTENSIONS = {'.png', '.bmp', '.gif', '.jpg', '.jpeg', '.webp'}
# Qt 的 Windows Media Foundation 后端稳定支持的容器。webm/mkv 依赖系统
# 额外解码器，很多机器上只能解出黑帧，因此不再作为背景视频格式。
VIDEO_EXTENSIONS = {'.mp4', '.m4v', '.mov', '.wmv', '.avi'}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
FAILED_VIDEO_CACHE = 'failed_background_videos.json'
FIT_OPTIONS = [('auto', '自动'), ('tile', '平铺'), ('center', '居中'), ('stretch', '拉伸'), ('top_left', '左上（保持长宽比）'), ('top_center', '中上（保持长宽比）'), ('top_right', '右上（保持长宽比）'), ('middle_left', '左中（保持长宽比）'), ('middle_center', '居中（保持长宽比）'), ('middle_right', '右中（保持长宽比）'), ('bottom_left', '左下（保持长宽比）'), ('bottom_center', '中下（保持长宽比）'), ('bottom_right', '右下（保持长宽比）')]
LOOP_MODES = [('image_only', '仅图片'), ('video_only', '仅视频'), ('sequential', '顺序'), ('random', '随机'), ('all_sequential', '全部顺序'), ('all_random', '全部随机')]
DEFAULT_LOOP_MODE = 'all_sequential'

def background_dir() -> Path:
    path = scl_dir() / 'background'
    path.mkdir(parents=True, exist_ok=True)
    return path

def background_dirs() -> List[Path]:
    '''启动器背景文件夹（SCL/background）。'''

    return [background_dir()]

def pictures_dir() -> Path:
    return background_dir()

def pictures_dirs() -> List[Path]:
    return background_dirs()

def _failed_video_cache_path() -> Path:
    return scl_dir() / 'cache' / FAILED_VIDEO_CACHE

def _video_key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))

def _video_signature(path):
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return int(stat.st_size), int(stat.st_mtime_ns)

def _read_failed_videos() -> dict:
    try:
        data = json.loads(
            _failed_video_cache_path().read_text(encoding='utf-8')
        )
    except (OSError, ValueError, TypeError):
        return {}
    entries = data.get('entries') if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}

def _write_failed_videos(entries: dict):
    if not isinstance(entries, dict):
        return
    path = _failed_video_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(
            json.dumps(
                {'schema': 1, 'entries': entries},
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )
        os.replace(temp, path)
    except OSError:
        pass

def _live_failed_videos() -> dict:
    '''返回仍然有效的失败记录；文件被替换/删除后自动允许重试。'''

    entries = _read_failed_videos()
    live = {}
    changed = False
    for key, row in entries.items():
        if not isinstance(row, dict):
            changed = True
            continue
        path = Path(str(row.get('path') or key))
        signature = _video_signature(path)
        if signature is None:
            changed = True
            continue
        try:
            same = (
                int(row.get('size')) == signature[0]
                and int(row.get('mtime_ns')) == signature[1]
            )
        except (TypeError, ValueError):
            same = False
        if same:
            live[key] = row
        else:
            changed = True
    if changed:
        _write_failed_videos(live)
    return live

def mark_background_video_failed(path, reason=''):
    '''持久记录无法播放的背景视频；下次启动不再重试同一文件。'''

    path = Path(str(path))
    signature = _video_signature(path)
    if signature is None:
        return
    entries = _live_failed_videos()
    entries[_video_key(path)] = {
        'path': str(path.resolve()),
        'size': signature[0],
        'mtime_ns': signature[1],
        'reason': str(reason or '')[:300],
        'time': time.time(),
    }
    if len(entries) > 200:
        newest = sorted(
            entries.items(),
            key=lambda item: float(item[1].get('time') or 0),
            reverse=True,
        )[:200]
        entries = dict(newest)
    _write_failed_videos(entries)

def clear_background_video_failed(path):
    entries = _live_failed_videos()
    if entries.pop(_video_key(path), None) is not None:
        _write_failed_videos(entries)

def is_background_video_failed(path) -> bool:
    if Path(str(path)).suffix.lower() not in VIDEO_EXTENSIONS:
        return False
    return _video_key(path) in _live_failed_videos()

def list_background_images() -> List[Path]:
    failed = _live_failed_videos()
    files = []
    for directory in background_dirs():
        try:
            for path in directory.iterdir():
                if not path.is_file():
                    continue
                suffix = path.suffix.lower()
                if suffix not in MEDIA_EXTENSIONS:
                    continue
                if (
                    suffix in VIDEO_EXTENSIONS
                    and _video_key(path) in failed
                ):
                    continue
                files.append(path)
        except OSError:
            continue
    files.sort(key=(lambda p: (p.name.lower(), str(p.parent).lower())))
    return files

def get_settings() -> dict:
    cfg = ConfigManager()
    return {'enabled': bool(cfg.get('background_enabled', True)), 'index': int(cfg.get('background_index', 0) or 0), 'fit': cfg.get('background_fit', 'auto') or 'auto', 'opacity': int(cfg.get('background_opacity', 80) or 80), 'blur': int(cfg.get('background_blur', 0) or 0), 'overlay': bool(cfg.get('background_overlay', True)), 'ui_depth': int(cfg.get('background_ui_depth', 0) or 0), 'loop_mode': str(cfg.get('background_loop_mode', DEFAULT_LOOP_MODE) or DEFAULT_LOOP_MODE), 'pause_video_on_background': bool(cfg.get('background_video_pause_on_background', True))}

def save_setting(key: str, value):
    ConfigManager().set(key, value)

def _is_video(path) -> bool:
    return bool(path and Path(path).suffix.lower() in VIDEO_EXTENSIONS)

def _eligible_media(files, mode, current=None):
    images = [path for path in files if path.suffix.lower() in IMAGE_EXTENSIONS]
    videos = [path for path in files if path.suffix.lower() in VIDEO_EXTENSIONS]
    if mode == 'image_only':
        return images
    if mode == 'video_only':
        return videos
    if mode in ('all_sequential', 'all_random'):
        return files
    if mode in ('sequential', 'random'):
        if current is not None and _is_video(current):
            return videos or files
        return images or files
    return files

def current_image() -> Path:
    images = list_background_images()
    if not images:
        return
    cfg = ConfigManager()
    settings = get_settings()
    current = images[settings['index'] % len(images)]
    eligible = _eligible_media(images, settings['loop_mode'], current)
    if not eligible:
        return
    if current not in eligible:
        current = eligible[0]
        try:
            cfg.set('background_index', images.index(current))
        except ValueError:
            pass
    return current

def next_image() -> Path:
    images = list_background_images()
    cfg = ConfigManager()
    if not images:
        cfg.set('background_index', 0)
        return
    settings = get_settings()
    mode = settings['loop_mode']
    current = images[settings['index'] % len(images)]
    eligible = _eligible_media(images, mode, current)
    if not eligible:
        return
    if len(eligible) == 1:
        chosen = eligible[0]
    elif mode in ('random', 'all_random'):
        # 随机模式避免连续选中同一项；只有一项时才会重复。
        pool = [path for path in eligible if path != current]
        chosen = random.choice(pool or eligible)
    else:
        try:
            index = eligible.index(current)
        except ValueError:
            index = -1
        chosen = eligible[(index + 1) % len(eligible)]
    try:
        cfg.set('background_index', images.index(chosen))
    except ValueError:
        return chosen
    return chosen

