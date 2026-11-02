'''主题相关的富文本颜色工具。

QLabel 的富文本内联颜色不会被全局 QSS 反色逻辑处理，因此需要在当前
主题下显式选择深色/浅色文字色，并在主题切换时重新生成文本。
'''

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from core.mod_translations import bilingual_html
from ui.design import MUTED_DARK, MUTED_LIGHT, TEXT_DARK, TEXT_LIGHT


def is_light_theme() -> bool:
    try:
        palette = QApplication.palette()
        return palette.color(QPalette.Window).lightness() > 128
    except Exception:
        return False


def text_colors() -> tuple[str, str]:
    if is_light_theme():
        return TEXT_LIGHT, MUTED_LIGHT
    return TEXT_DARK, MUTED_DARK


def themed_bilingual_html(zh, en, primary_size=13, secondary_size=11) -> str:
    primary, secondary = text_colors()
    return bilingual_html(
        zh, en, primary_size, secondary_size,
        primary_color=primary, secondary_color=secondary,
    )


def themed_color(secondary=False) -> str:
    primary, muted = text_colors()
    return muted if secondary else primary
