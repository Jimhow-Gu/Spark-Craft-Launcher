'''通用表单控件样式。'''

# 不要给 QSlider 设置 QSS：QStyleSheetStyle 会绕过自定义 QProxyStyle 的圆角滑轨和滑钮。
SLIDER_STYLE = ''

RADIO_BUTTON_STYLE = '''
QRadioButton {
    color: #f0f0f0;
    spacing: 8px;
    background: transparent;
}
'''
