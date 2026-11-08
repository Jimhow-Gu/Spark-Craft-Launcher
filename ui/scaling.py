'''窗口缩小时只压缩控件尺寸，不改变布局间距。'''

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QSlider, QWidget,
)

BASE_WIDTH = 1280.0
MIN_CONTROL_SCALE = 0.72
MAX_CONTROL_SCALE = 1.0
_SCALE_STEP = 0.02
_BASE_PROPERTY = '_scl_control_base'


def control_scale(window_width):
    raw = float(window_width) / BASE_WIDTH
    raw = max(MIN_CONTROL_SCALE, min(MAX_CONTROL_SCALE, raw))
    # 量化到 0.02，拖动窗口时避免每一像素都重新布置整页控件。
    return round(round(raw / _SCALE_STEP) * _SCALE_STEP, 2)


def _is_scalable(widget):
    if bool(widget.property('_scl_scalable')):
        return True
    return isinstance(
        widget,
        (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox, QSlider),
    )


def _base_metrics(widget):
    base = widget.property(_BASE_PROPERTY)
    if base is None:
        base = [
            int(widget.minimumWidth()),
            int(widget.minimumHeight()),
            int(widget.maximumWidth()),
            int(widget.maximumHeight()),
        ]
        widget.setProperty(_BASE_PROPERTY, base)
    return base


def _scaled(value, scale, minimum=0):
    return max(int(minimum), int(round(int(value) * float(scale))))


def _scaled_max(value, scale):
    if value <= 0:
        return value
    return _scaled(value, scale, 20)


def _apply_size(widget, scale):
    base_min_w, base_min_h, base_max_w, base_max_h = _base_metrics(widget)
    max_widget = 16777215

    fixed_w = base_min_w > 0 and base_min_w == base_max_w
    fixed_h = base_min_h > 0 and base_min_h == base_max_h
    if fixed_w:
        widget.setFixedWidth(_scaled(base_min_w, scale, 20))
    else:
        new_min_w = _scaled(base_min_w, scale, 0) if base_min_w > 0 else 0
        new_max_w = _scaled_max(base_max_w, scale)
        if scale >= 1.0:
            if new_max_w < max_widget:
                widget.setMaximumWidth(new_max_w)
            widget.setMinimumWidth(new_min_w)
        else:
            widget.setMinimumWidth(new_min_w)
            if new_max_w < max_widget:
                widget.setMaximumWidth(new_max_w)

    if fixed_h:
        widget.setFixedHeight(_scaled(base_min_h, scale, 20))
    else:
        new_min_h = _scaled(base_min_h, scale, 0) if base_min_h > 0 else 0
        new_max_h = _scaled_max(base_max_h, scale)
        if scale >= 1.0:
            if new_max_h < max_widget:
                widget.setMaximumHeight(new_max_h)
            widget.setMinimumHeight(new_min_h)
        else:
            widget.setMinimumHeight(new_min_h)
            if new_max_h < max_widget:
                widget.setMaximumHeight(new_max_h)


def apply_widget_scale(widget, scale):
    '''缩放单个控件；用于页面加载完毕后动态创建的按钮。'''

    if widget is None or not _is_scalable(widget):
        return False
    try:
        _apply_size(widget, float(scale))
        return True
    except RuntimeError:
        return False



def apply_control_scale(root, scale):
    '''按比例调整 root 下按钮、输入框等控件的尺寸；布局 spacing 保持不变。'''

    if root is None:
        return
    scale = max(MIN_CONTROL_SCALE, min(MAX_CONTROL_SCALE, float(scale)))
    try:
        widgets = root.findChildren(QWidget)
    except RuntimeError:
        return
    for widget in widgets:
        if not _is_scalable(widget):
            continue
        try:
            _apply_size(widget, scale)
        except RuntimeError:
            continue
