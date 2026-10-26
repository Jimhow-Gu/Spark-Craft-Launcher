'''阻止鼠标滚轮误操作下拉框/滑块；列表滚动交还给 Qt 默认处理。'''

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QComboBox, QSlider

class WheelGuard(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and isinstance(obj, (QComboBox, QSlider)):
            event.ignore()
            return True
        return super().eventFilter(obj, event)


