'''启动前的 Java 检查弹窗。'''

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from ui.widgets.dark_dialog import DarkQuestionDialog


def ask_java_missing(parent, java_major=17):
    '''提示没有可用 Java；由启动器下载并安装，返回可用的 java 路径。'''

    dialog = DarkQuestionDialog(
        parent, '未找到 Java',
        f'当前没有可用的 Java {int(java_major)}。\n\n'
        f'可以让启动器自动下载 Java {int(java_major)} 并用于当前版本。',
        actions=[('取消', 'cancel', False), (f'下载 Java {int(java_major)}', 'download', True)],
    )
    dialog.exec()
    if dialog.choice != 'download':
        return ''
    from ui.widgets.java_download_dialog import JavaDownloadDialog
    downloader = JavaDownloadDialog(parent, int(java_major))
    downloader.exec()
    if downloader.java_path:
        return downloader.java_path
    if downloader.error:
        QMessageBox.warning(parent, 'Java 下载失败', downloader.error)
    return ''


def ask_java_mismatch(parent, current_major, min_major, max_major):
    '''返回 continue（继续启动）或 auto（自动选择最佳版本）。'''

    if current_major < min_major:
        detail = f'最低适配版本:JAVA{min_major}'
    else:
        detail = f'最高适配版本:JAVA{max_major}'
    text = f'当前版本与所选JAVA不匹配，当前:JAVA{current_major},{detail}。\n\n继续启动可能导致游戏崩溃，建议自动选择最佳版本。'
    dialog = DarkQuestionDialog(parent, 'Java 版本不匹配', text, actions=[('继续启动', 'continue', False), ('自动选择最佳版本', 'auto', True)])
    dialog.exec()
    if dialog.choice in ('continue', 'auto'):
        return dialog.choice
    return 'cancel'
