'''启动器背景图片设置（统一存放在 SCL/background）。'''

from pathlib import Path
from typing import List
from core.config import ConfigManager
from core.paths import scl_dir
IMAGE_EXTENSIONS = {'.png', '.bmp', '.gif', '.jpg', '.jpeg', '.webp'}
VIDEO_EXTENSIONS = {'.webm', '.mov', '.mp4', '.avi', '.mkv', '.m4v'}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
FIT_OPTIONS = [('auto', '自动'), ('tile', '平铺'), ('center', '居中'), ('stretch', '拉伸'), ('top_left', '左上（保持长宽比）'), ('top_center', '中上（保持长宽比）'), ('top_right', '右上（保持长宽比）'), ('middle_left', '左中（保持长宽比）'), ('middle_center', '居中（保持长宽比）'), ('middle_right', '右中（保持长宽比）'), ('bottom_left', '左下（保持长宽比）'), ('bottom_center', '中下（保持长宽比）'), ('bottom_right', '右下（保持长宽比）')]

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
    return {'enabled': bool(cfg.get('background_enabled', True)), 'index': int(cfg.get('background_index', 0) or 0), 'fit': cfg.get('background_fit', 'auto') or 'auto', 'opacity': int(cfg.get('background_opacity', 80) or 80), 'blur': int(cfg.get('background_blur', 0) or 0), 'overlay': bool(cfg.get('background_overlay', True)), 'ui_depth': int(cfg.get('background_ui_depth', 0) or 0), 'pause_video_on_background': bool(cfg.get('background_video_pause_on_background', True))}

def save_setting(key: str, value):
    ConfigManager().set(key, value)

def current_image() -> Path:
    images = list_background_images()
    if not images:
        return
    settings = get_settings()
    return images[settings['index'] % len(images)]

def next_image() -> Path:
    images = list_background_images()
    cfg = ConfigManager()
    if not images:
        cfg.set('background_index', 0)
        return
    index = (int(cfg.get('background_index', 0) or 0) + 1) % len(images)
    cfg.set('background_index', index)
    return images[index]

