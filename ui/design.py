'''统一设计令牌：薄荷青主色、圆角、间距与控制样式。

页面只应引用这里的颜色/圆角常量，或使用全局控件样式；
不要再在页面里散落微软蓝等独立色值。
'''

from __future__ import annotations

import re

# 品牌主色：与标题栏、Logo 的青色保持一致。
ACCENT = '#88DDDD'
ACCENT_HOVER = '#66BBBB'
ACCENT_PRESSED = '#409999'
ACCENT_TEXT = '#A8E6E6'
ON_ACCENT = '#122828'
ACCENT_SOFT = 'rgba(136,221,221,0.16)'
ACCENT_SELECTION = 'rgba(136,221,221,0.30)'

# 交互控件主色：滑块、主按钮、勾选框和深色侧栏选中态。
PRIMARY = '#90D9D9'
PRIMARY_HOVER = '#78CACA'
PRIMARY_PRESSED = '#5FAFAF'
PRIMARY_SOFT = 'rgba(144,217,217,0.18)'

# 两套主题的核心表面/文字色。
WINDOW_DARK = '#121717'
CARD_DARK = '#1E2929'
SIDEBAR_DARK = '#273838'
TITLEBAR_DARK = '#121717'
TEXT_DARK = '#EEFFFF'
MUTED_DARK = '#A8C8C8'
DEEP_ACCENT_DARK = '#0F4C4C'

WINDOW_LIGHT = '#FFFFFF'
CARD_LIGHT = '#F8FBFB'
SIDEBAR_LIGHT = '#E6F3F3'
TITLEBAR_LIGHT = '#F8FBFB'
TEXT_LIGHT = '#222222'
MUTED_LIGHT = '#555555'
DEEP_ACCENT_LIGHT = '#409999'

# 统一圆角/间距尺度（px）。
RADIUS_SM = 6
RADIUS_MD = 8
RADIUS_LG = 12
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 14
SPACE_LG = 20

# 旧版微软蓝及蓝色系 → 薄荷青。运行时也会对页面里遗留的 QSS 做一次归一化。
_HEX_MAP = {
    '#0078d4': ACCENT,
    '#1084e0': ACCENT_HOVER,
    '#106ebe': ACCENT_HOVER,
    '#006cbd': ACCENT_PRESSED,
    '#005a9e': ACCENT_PRESSED,
    '#4da3ff': ACCENT_TEXT,
    '#7aa0ff': ACCENT,
    '#9ccaff': ACCENT_TEXT,
    '#8ec8ff': ACCENT_TEXT,
    '#8ec5ff': ACCENT_TEXT,
    '#7fc4ff': ACCENT_TEXT,
    '#67b7ff': ACCENT_TEXT,
    '#3f8ae0': ACCENT,
    '#2f5f86': '#3A7777',
    '#2f6fa8': '#3A7777',
    # 上一版薄荷青令牌兼容
    '#5eead4': ACCENT,
    '#7df0dd': ACCENT_HOVER,
    '#3ed3bd': ACCENT_PRESSED,
    '#8af5e4': ACCENT_TEXT,
    '#a7f3e8': ACCENT_TEXT,
    '#b8f0f0': ACCENT_TEXT,
    '#06231e': ON_ACCENT,
}

# 把旧页面里散落的蓝灰表面收敛到两套主题的卡片/侧栏色系。
_NEUTRAL_MAP = {
    '#3a3a3a': '#273838',
    '#2a2a2a': CARD_DARK,
    '#4a4a4a': '#2F4747',
    '#20232b': CARD_DARK,
    '#24262c': CARD_DARK,
    '#202020': CARD_DARK,
    '#1f2229': WINDOW_DARK,
    '#2a2d36': CARD_DARK,
    '#30333b': '#273838',
    '#3a3d47': '#273838',
    '#3a4050': '#273838',
    '#3b414a': '#273838',
    '#222233': WINDOW_DARK,
    '#202124': CARD_DARK,
    '#171717': WINDOW_DARK,
    '#1e1e1e': WINDOW_DARK,
    '#1b2527': CARD_DARK,
    '#213333': SIDEBAR_DARK,
    # 旧页面的近白色文字统一到主文字色，灰蓝色统一到次要文字色。
    '#f0f0f0': TEXT_DARK,
    '#f0f8f8': TEXT_DARK,
    '#e8ebf1': TEXT_DARK,
    '#dddddd': TEXT_DARK,
    '#ddd': TEXT_DARK,
    '#d7d7d7': TEXT_DARK,
    '#a8a8a8': MUTED_DARK,
    '#9a9a9a': MUTED_DARK,
    '#8b929d': MUTED_DARK,
    '#858a94': MUTED_DARK,
}

# 常见的深色半透明面板/列表背景 → 品牌卡片/侧栏色。
_NEUTRAL_RGBA_MAP = {
    (18, 20, 26): ((30, 41, 41), 0.84),
    (28, 31, 40): ((39, 56, 56), 0.90),
    (28, 30, 38): ((39, 56, 56), 0.90),
    (10, 12, 18): ((30, 41, 41), 0.62),
    (20, 22, 28): ((18, 23, 23), 0.94),
    (8, 10, 16): ((18, 23, 23), 0.72),
    (15, 17, 23): ((18, 23, 23), 0.90),
}

_COLOR_RE = re.compile(
    r'#(?:0078d4|1084e0|106ebe|006cbd|005a9e|4da3ff|7aa0ff|9ccaff|'
    r'8ec8ff|8ec5ff|7fc4ff|67b7ff|3f8ae0|2f5f86|2f6fa8|'
    r'5eead4|7df0dd|3ed3bd|8af5e4|a7f3e8|06231e|'
    + '|'.join(key[1:] for key in _NEUTRAL_MAP) + r')\b',
    re.I,
)
_RGBA_BLUE_RE = re.compile(
    r'rgba?\(\s*(?:0\s*,\s*120\s*,\s*212|122\s*,\s*160\s*,\s*255|'
    r'94\s*,\s*234\s*,\s*212)\s*,\s*([^)]+)\)',
    re.I,
)
_NEUTRAL_RGBA_RE = re.compile(r'rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([^)]+)\)', re.I)
_RADIUS_RE = re.compile(r'(border-radius\s*:\s*)(5|7|9|11)px', re.I)
_SCROLLBAR_RULE_RE = re.compile(r'(?is)[^{}]*QScrollBar[^{}]*\{[^{}]*\}')
_RULE_RE = re.compile(r'(?s)([^{}]+)\{([^{}]*)\}')
_WHITE_TEXT_RE = re.compile(
    r'(?i)(\b(?:color|selection-color)\s*:\s*)'
    r'(white|#fff(?:fff)?|rgba?\(\s*255\s*,\s*255\s*,\s*255[^)]*\))'
)

_STRONG_ACCENT_RE = re.compile(
    r'(?i)(?:background(?:-color)?\s*:\s*[^;}]*)'
    r'(?:#88dddd|#66bbbb|#50aaaa|#409999|#90d9d9|#78caca|#5fafaf|rgba?\(\s*136\s*,\s*221\s*,\s*221\s*,\s*(?:0?\.(?:[5-9]\d?)|1(?:\.0+)?)\s*\))'
)


def accent_qss(qss: str) -> str:
    '''把旧 QSS 的蓝色系、滚动条和零散圆角统一到设计令牌。'''

    if not qss:
        return qss
    text = str(qss)

    def replace_hex(match):
        key = match.group(0).lower()
        return _HEX_MAP.get(key) or _NEUTRAL_MAP.get(key) or match.group(0)

    text = _COLOR_RE.sub(replace_hex, text)
    text = _RGBA_BLUE_RE.sub(lambda m: f'rgba(136,221,221,{m.group(1).strip()})', text)

    def replace_neutral_rgba(match):
        key = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        target = _NEUTRAL_RGBA_MAP.get(key)
        if not target:
            return match.group(0)
        (r, g, b), alpha = target
        return f'rgba({r},{g},{b},{alpha})'

    text = _NEUTRAL_RGBA_RE.sub(replace_neutral_rgba, text)
    # 页面自定义滚动条交给 ScrollBarStyler 统一绘制，避免各页宽度/颜色不一致。
    text = _SCROLLBAR_RULE_RE.sub('', text)
    # 主色按钮上的白字改为深色，保证薄荷青底色上的对比度。
    def fix_rule(match):
        selector, body = match.group(1), match.group(2)
        if _STRONG_ACCENT_RE.search(body):
            body = _WHITE_TEXT_RE.sub(lambda m: m.group(1) + ON_ACCENT, body)
        return selector + '{' + body + '}'

    text = _RULE_RE.sub(fix_rule, text)
    text = _RADIUS_RE.sub(lambda m: m.group(1) + str({5: 6, 7: 8, 9: 10, 11: 12}[int(m.group(2))]) + 'px', text)
    return text


def app_stylesheet(light: bool = False) -> str:
    '''应用级基础控件样式：只负责圆角/间距/中性表面，不绑定明暗色。

    Qt 在切换全局 stylesheet 时会重排所有控件；把颜色交给调色板和
    页面令牌，这份样式只在启动时设置一次，主题切换才能保持毫秒级。
    light 参数保留用于兼容调用方。
    '''

    surface = 'rgba(128,128,128,0.13)'
    surface_hover = 'rgba(128,128,128,0.22)'
    surface_pressed = 'rgba(128,128,128,0.09)'
    border = 'rgba(128,128,128,0.30)'
    track = 'rgba(128,128,128,0.20)'

    return f'''
QWidget {{
    selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT};
}}
QPushButton {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {RADIUS_MD}px;
}}
QPushButton:hover {{ background: {surface_hover}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {surface_pressed}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {RADIUS_MD}px;
    padding: 6px 10px;
    min-height: 22px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {RADIUS_MD}px;
    padding: 7px 16px;
    margin-right: 6px;
}}
QTabBar::tab:selected {{ background: {ACCENT}; color: {ON_ACCENT}; border-color: {ACCENT}; }}
QTabBar::tab:hover {{ border-color: {ACCENT}; }}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QGroupBox {{
    background: transparent;
    border: 1px solid {border};
    border-radius: {RADIUS_LG}px;
    margin-top: 8px;
    padding-top: 12px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 5px; }}
QToolButton {{ background: transparent; border: none; border-radius: {RADIUS_SM}px; padding: 5px; }}
QToolButton:hover {{ background: {surface_hover}; }}
QProgressBar {{
    background: {track};
    border: none;
    border-radius: 4px;
    min-height: 8px;
    max-height: 8px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QListWidget, QListView, QTreeView, QTableView {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {RADIUS_MD}px;
    outline: none;
}}
QListWidget::item, QListView::item, QTreeView::item, QTableView::item {{
    padding: 5px 8px;
    border-radius: {RADIUS_SM}px;
}}
QListWidget::item:selected, QListView::item:selected, QTreeView::item:selected, QTableView::item:selected {{
    background: {ACCENT};
    color: {ON_ACCENT};
}}
QHeaderView::section {{
    background: {surface};
    border: none;
    border-bottom: 1px solid {border};
    padding: 6px 8px;
}}
'''


__all__ = [
    'ACCENT', 'ACCENT_HOVER', 'ACCENT_PRESSED', 'ACCENT_TEXT', 'ON_ACCENT',
    'ACCENT_SOFT', 'ACCENT_SELECTION',
    'PRIMARY', 'PRIMARY_HOVER', 'PRIMARY_PRESSED', 'PRIMARY_SOFT',
    'RADIUS_SM', 'RADIUS_MD', 'RADIUS_LG',
    'SPACE_XS', 'SPACE_SM', 'SPACE_MD', 'SPACE_LG',
    'WINDOW_DARK', 'CARD_DARK', 'SIDEBAR_DARK', 'TITLEBAR_DARK',
    'TEXT_DARK', 'MUTED_DARK', 'DEEP_ACCENT_DARK',
    'WINDOW_LIGHT', 'CARD_LIGHT', 'SIDEBAR_LIGHT', 'TITLEBAR_LIGHT',
    'TEXT_LIGHT', 'MUTED_LIGHT', 'DEEP_ACCENT_LIGHT',
    'accent_qss', 'app_stylesheet',
]
