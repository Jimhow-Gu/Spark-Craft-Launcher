'''轻量发布常量：启动窗口只读这些值，避免提前导入网络/更新模块。'''

CURRENT_VERSION = '1.11.4'
VERSION_DISPLAY = 'V1.11.4（正式版）'
# DLL 构建号在同一版本内递增；每次发布新大版本时重置为 1。
EXECUTABLE_BUILD = '47'

__all__ = ['CURRENT_VERSION', 'VERSION_DISPLAY', 'EXECUTABLE_BUILD']
