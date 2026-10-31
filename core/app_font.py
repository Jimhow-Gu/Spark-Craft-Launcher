'''加载启动器 font/ 目录中的字体，并统一 UI 字体。'''

from __future__ import annotations
from pathlib import Path
from typing import List
from PySide6.QtGui import QFont, QFontDatabase
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = PROJECT_ROOT / 'font'
FONT_DIR.mkdir(parents=True, exist_ok=True)
_UI_FONT_FAMILY = ''

def load_ui_fonts():
    global _UI_FONT_FAMILY
    if _UI_FONT_FAMILY:
        return _UI_FONT_FAMILY
    families = []
    try:
        candidates = sorted([p for p in FONT_DIR.iterdir() if p.is_file() if p.suffix.lower() in ('.ttf', '.otf', '.ttc')], key=(lambda p: p.name.lower()))
    except OSError:
        candidates = []
    for path in candidates:
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            for family in QFontDatabase.applicationFontFamilies(font_id):
                if family not in families:
                    families.append(family)
    for family in families:
        if 'HarmonyOS' in family or '鸿蒙' in family:
            _UI_FONT_FAMILY = family
            break
    if not _UI_FONT_FAMILY:
        if families:
            _UI_FONT_FAMILY = families[0]
    if not _UI_FONT_FAMILY:
        _UI_FONT_FAMILY = 'Segoe UI'
    return _UI_FONT_FAMILY

def ui_font(size, weight=QFont.Normal):
    # 统一使用 HarmonyOS Sans SC Black，忽略调用处的粗细参数。
    font = QFont(_UI_FONT_FAMILY or 'Segoe UI', size)
    font.setWeight(QFont.Black)
    try:
        font.setStyleName('Black')
    except Exception:
        pass
    return font

