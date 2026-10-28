'''启动器背景图片设置（统一存放在 SCL/background）。'''

import random
from pathlib import Path
from typing import List
from core.config import ConfigManager
from core.paths import scl_dir
IMAGE_EXTENSIONS = {'.png', '.bmp', '.gif', '.jpg', '.jpeg', '.webp'}
VIDEO_EXTENSIONS = {'.webm', '.mov', '.mp4', '.avi', '.mkv', '.m4v'}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
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

def list_background_images() -> List[Path]:
    files = []
    for directory in background_dirs():
        try:
            for path in directory.iterdir():
                if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
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

