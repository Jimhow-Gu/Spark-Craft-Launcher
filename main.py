import os
os.environ["QT_OPENGL"] = "software"   # 降低 OpenGL 栈冲突风险
# 使用 Windows Media Foundation，避免打包 Qt 自带的 FFmpeg 运行库。
# 必须在任何 PySide6.QtMultimedia 导入之前设置。
os.environ["QT_MEDIA_BACKEND"] = "windows"

import ctypes
import logging
import sys
from pathlib import Path


def _prepare_qt_runtime():
    """固定 Qt 插件和 DLL 搜索到 PyInstaller 解包目录，避免外部同名插件劫持。"""

    if not getattr(sys, "frozen", False):
        return
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    plugins = base / "PySide6" / "plugins"
    platforms = plugins / "platforms"
    if platforms.is_dir():
        os.environ["QT_PLUGIN_PATH"] = str(plugins)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platforms)
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetDllDirectoryW(str(base))
        except Exception:
            pass


_prepare_qt_runtime()

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QStyleFactory
from PySide6.QtCore import Qt, QObject, QEvent, QTimer
from PySide6.QtGui import QFont, QIcon

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.single_instance import (acquire_single_instance, release_single_instance, send_activation_request, start_activation_server, stop_activation_server)


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return base / relative


from core.paths import (
    migrate_legacy_dirs, ensure_runtime_dirs, legacy_dirs_pending, scl_dir,
)
from core.logging_setup import setup_logging
setup_logging()

from ui.main_window import MainWindow
from core.config import ConfigManager


def _shutdown_java_scan_service():
    # 不在启动时导入/实例化扫描服务；只有它确实被用过才关闭后台线程。
    try:
        from core import java_scan
        service = getattr(java_scan.JavaScanService, '_instance', None)
        if service is not None:
            service.shutdown()
    except Exception:
        pass


class DialogIconFilter(QObject):
    """所有次级窗口（对话框/消息框）不显示启动器程序图标。"""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, (QDialog, QMessageBox)):
            try:
                obj.setWindowIcon(QIcon())
            except Exception:
                pass
        return False


def set_taskbar_identity():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "SparkCraft.Launcher.1"
        )
    except Exception:
        pass


def apply_packaged_defaults_once():
    """兼容旧版启动流程；更新后不再重置主题和其他用户设置。"""

    return


def launcher_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ensure_launcher_minecraft():
    mc_dir = launcher_base_dir() / ".minecraft"
    if not mc_dir.exists():
        mc_dir.mkdir(parents=True, exist_ok=True)
        print(f"[启动] 已在启动器目录创建 .minecraft: {mc_dir}")
    try:
        (mc_dir / "versions").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return mc_dir


def init_game_dirs():
    config = ConfigManager()
    launcher_mc = ensure_launcher_minecraft()
    launcher_path = str(launcher_mc)
    appdata_mc = Path(os.environ.get("APPDATA", "")) / ".minecraft"

    # 保留用户已经添加过的目录，只补充启动器同级目录和标准 .minecraft。
    raw_dirs = list(config.get("game_dirs", []) or [])
    raw_dirs.extend([launcher_path, str(appdata_mc)])
    dirs = []
    seen = set()
    for path in raw_dirs:
        text = str(path or "").strip()
        if not text:
            continue
        key = os.path.normcase(os.path.abspath(text))
        if key in seen:
            continue
        seen.add(key)
        dirs.append(text)
    config.set("game_dirs", dirs)

    selected = str(
        config.get("selected_game_dir", "")
        or config.get("mc_dir", "")
        or ""
    )
    if not selected or not Path(selected).is_dir():
        selected = launcher_path
        config.set("selected_game_dir", selected)
    selected_key = os.path.normcase(os.path.abspath(selected))
    if selected_key not in seen:
        dirs.insert(0, selected)
        config.set("game_dirs", dirs)
    config.set("mc_dir", selected)
    config.save()
    print(f"[启动] 游戏目录列表: {dirs}")
    print(f"[启动] 当前选中: {selected}")
    return selected

def _run_prewarm():
    """安装后预热：加载 Qt 与页面模块，不创建窗口、不读账号配置。

    Windows 首次执行新文件时会做实时扫描；让安装程序先跑一次这个轻量
    进程，用户第一次正常启动即可复用系统文件缓存。
    """

    import threading
    watchdog = threading.Timer(10.0, lambda: os._exit(0))
    watchdog.daemon = True
    watchdog.start()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    for module in (
        'PySide6.QtMultimedia',
        'PySide6.QtNetwork',
        'core.java_download',
        'core.forge_artifacts',
        'core.launch',
        'ui.pages.home_page',
        'ui.pages.version_page',
        'ui.pages.account_page',
        'ui.pages.settings_page',
        'ui.pages.about_page',
        'ui.pages.download_manager_page',
        'ui.pages.news_page',
        'ui.pages.news_detail',
        'ui.pages.game_settings_page',
        'ui.pages.loader_modify_page',
        'ui.pages.mod_browser_page',
        'ui.pages.mod_manager_dialog',
        'ui.pages.modpack_import_page',
        'ui.pages.version_detail_page',
    ):
        try:
            __import__(module)
        except Exception:
            pass
    app.processEvents()
    sys.exit(0)


def _show_launch_error(message):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(message), '启动失败', 0x10)
    except Exception:
        print(message, file=sys.stderr)


def _run_exported_launch(token):
    """导出的 bat 只传安全 token，在这里解析版本并启动 Java。"""

    import base64
    import subprocess
    try:
        raw = str(token or '').strip()
        raw += '=' * (-len(raw) % 4)
        version_id = base64.urlsafe_b64decode(raw.encode('ascii')).decode('utf-8')
    except Exception:
        _show_launch_error('启动脚本参数损坏，请重新导出。')
        return 1
    from core.config import ConfigManager
    from core.launch import LaunchManager
    manager = LaunchManager()
    command, error = manager.build_launch_command(version_id, prompt_java=False)
    if not command:
        _show_launch_error(error or '无法生成启动命令')
        return 1
    try:
        working_dir = manager.get_game_dir(version_id)
        subprocess.Popen(
            command,
            cwd=str(working_dir),
            close_fds=True,
            env=os.environ.copy(),
        )
        return 0
    except Exception as exc:
        _show_launch_error(f'启动游戏失败：{exc}')
        return 1


def main():
    if '--prewarm' in sys.argv[1:]:
        _run_prewarm()
        return
    if '--launch-b64' in sys.argv[1:]:
        index = sys.argv.index('--launch-b64')
        token = sys.argv[index + 1] if index + 1 < len(sys.argv) else ''
        sys.exit(_run_exported_launch(token))
    for argument in sys.argv[1:]:
        if argument.startswith('--launch-b64='):
            sys.exit(_run_exported_launch(argument.split('=', 1)[1]))
    set_taskbar_identity()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    # 机房/校园网通常通过系统代理上网；QtNetwork 默认不读取 Windows 代理，
    # 会导致登录和下载正常、头像却一直空白。这里统一启用系统代理。
    try:
        from PySide6.QtNetwork import QNetworkProxyFactory
        QNetworkProxyFactory.setUseSystemConfiguration(True)
    except Exception:
        pass
    from core.error_guard import install_error_guard
    install_error_guard(app)
    try:
        from core.thread_guard import install_thread_guard
        install_thread_guard(app)
    except Exception:
        pass
    from ui.widgets.checkbox_style import CheckBoxProxyStyle
    # Windows 10 的 Vista 原生样式会忽略自定义暗色 QPalette，导致勾选框/
    # 单选框文字仍使用系统深浅色。Fusion 样式完全按调色板绘制，Win10/11
    # 主题表现一致；勾选框和滑条仍由 CheckBoxProxyStyle 自绘。
    fusion_style = QStyleFactory.create('Fusion')
    app._base_ui_style = fusion_style
    app.setStyle(
        CheckBoxProxyStyle(fusion_style) if fusion_style is not None
        else CheckBoxProxyStyle()
    )
    from ui.widgets.smooth_scroll import SmoothScroller
    app._smooth_scroller = SmoothScroller(app)
    app.installEventFilter(app._smooth_scroller)
    from ui.widgets.scrollbar_style import ScrollBarStyler
    app._scrollbar_styler = ScrollBarStyler(app)
    app.installEventFilter(app._scrollbar_styler)
    from ui.widgets.text_context_menu import TextContextMenuFilter
    app._text_context_menu = TextContextMenuFilter(app)
    app.installEventFilter(app._text_context_menu)
    from ui.widgets.themed_tooltip import install_themed_tooltips
    app._themed_tooltip_filter = install_themed_tooltips(app)
    app.aboutToQuit.connect(_shutdown_java_scan_service)
    app.aboutToQuit.connect(stop_activation_server)
    app.aboutToQuit.connect(release_single_instance)
    app.setApplicationName("Spark Craft Launcher")
    app.setApplicationDisplayName("Spark Craft Launcher")
    from core.release_info import CURRENT_VERSION
    app.setApplicationVersion(CURRENT_VERSION)
    startup_light = False
    # 启动图标跟随当前主题（配置优先，其次系统 AppsUseLightTheme）。
    # exe/固定快捷方式仍是 neutral，由 PyInstaller 编译期写入。
    try:
        from ui.theme import system_is_light
        try:
            configured_theme = str(
                ConfigManager().get('theme', 'system') or 'system'
            ).lower()
        except Exception:
            configured_theme = 'system'
        if configured_theme == 'light':
            startup_light = True
        elif configured_theme == 'dark':
            startup_light = False
        else:
            startup_light = bool(system_is_light())
        from ui.runtime_icons import apply_runtime_icon, runtime_icon_paths
        apply_runtime_icon(startup_light)
        ico_path = runtime_icon_paths(startup_light)[0]
    except Exception:
        # neutral 图标只编译进 PE，不随 onefile 解包；运行期回退到当前主题。
        suffix = 'light' if startup_light else 'dark'
        ico_path = resource_path(f'ui/imgs/sparkler_{suffix}.ico')
        QApplication.instance().setWindowIcon(QIcon(str(ico_path)))
    from core.app_font import load_ui_fonts, ui_font
    load_ui_fonts()
    app.setFont(ui_font(10))

    if not acquire_single_instance():
        logging.getLogger("Startup").info("检测到已有启动器，尝试激活已有窗口")
        if send_activation_request():
            sys.exit(0)
        logging.getLogger("Startup").warning("已有实例但激活请求失败，显示提示")
        from ui.widgets.dark_dialog import DarkQuestionDialog
        DarkQuestionDialog(
            None, "提示",
            "检测到启动器已经在运行，本次启动将自动退出。",
            actions=[("确定", "ok", True)],
        ).exec()
        logging.getLogger("Startup").warning("已确认重复启动，当前实例退出")
        sys.exit(0)
    logging.getLogger("Startup").info("单实例锁已获取")
    activation_state = {'window': None, 'pending': False}

    def activate_existing_window():
        target = activation_state.get('window')
        if target is None:
            activation_state['pending'] = True
            return
        try:
            if target.isMinimized():
                target.setWindowState(
                    target.windowState() & ~Qt.WindowMinimized
                )
            target.show()
            target.raise_()
            target.activateWindow()
        except Exception:
            pass

    activation_server = start_activation_server()
    if activation_server is not None:
        activation_server.activation_requested.connect(activate_existing_window)

    # 兜底：如果上次下载完更新但重启按钮没有生效，下次启动直接安装。
    # 先看轻量文件标记，正常启动不导入 requests/urllib3 更新栈。
    update_dir = scl_dir() / 'update'
    if (update_dir / 'downloaded_update.json').is_file():
        try:
            from core.update import (
                apply_downloaded_update_on_startup,
                downloaded_update_available,
            )
            if downloaded_update_available():
                logging.getLogger("Startup").info("发现已下载更新，尝试启动时安装")
                release_single_instance()
                if apply_downloaded_update_on_startup():
                    os._exit(0)
                acquire_single_instance()
        except Exception:
            pass

    # 更新时旧 exe 会被重命名为 .old。只有确实存在备份时才加载更新模块。
    leftover_old = []
    try:
        import threading
        target = Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else None
        has_old = bool(
            target is not None
            and (
                any(target.parent.glob(target.name + '*.old'))
                or (target.parent / '_internal.old').exists()
            )
        )
        if has_old:
            from core.update import cleanup_old_executable
            leftover_old = cleanup_old_executable(retries=4, retry_rounds=1) or []
            threading.Thread(
                target=cleanup_old_executable,
                kwargs={'retry_rounds': 9},
                daemon=True,
            ).start()
    except Exception:
        pass

    # 更新后首次启动时在页面读取缓存前刷新旧素材/新闻/翻译缓存。
    if (update_dir / 'updated_update.json').is_file():
        try:
            from core.update import pending_update_exists, clear_launcher_caches
            if pending_update_exists():
                clear_launcher_caches()
        except Exception:
            pass

    # 只有首个实例才整理 SCL 运行目录，避免第二个实例移动正在使用的文件。
    # 先用廉价存在性检查跳过绝大多数已是新版目录的启动。
    if legacy_dirs_pending():
        migrate_legacy_dirs()
    ensure_runtime_dirs()
    try:
        from core.java_download import cleanup_old_java_runtimes
        import threading as _startup_threading
        _startup_threading.Thread(
            target=cleanup_old_java_runtimes, daemon=True
        ).start()
    except Exception:
        pass

    rename_recovery_error = ''
    try:
        from core.version_rename import recover_pending_version_rename
        rename_recovery_error = recover_pending_version_rename(
            ConfigManager().get_mc_dir()
        )
    except Exception as exc:
        rename_recovery_error = f'版本重命名恢复失败：{exc}'

    from ui.widgets.dark_dialog import install_dark_message_boxes
    install_dark_message_boxes()
    dialog_icon_filter = DialogIconFilter(app)
    app.installEventFilter(dialog_icon_filter)

    apply_packaged_defaults_once()
    init_game_dirs()

    try:
        window = MainWindow()
    except Exception:
        logging.getLogger('Startup').exception('主窗口初始化失败')
        try:
            from PySide6.QtWidgets import QMessageBox
            from core.logging_setup import LOG_FILE
            QMessageBox.critical(
                None, '启动失败',
                '启动器界面初始化失败，请查看日志后重试。'
                + chr(10) * 2
                + '日志：'
                + str(LOG_FILE),
            )
        except Exception:
            pass
        sys.exit(1)
    window.setWindowIcon(app.windowIcon())
    # 原生窗口图标必须在窗口真正显示后再写。提前调用 winId() 会创建一次
    # 原生窗口，再由 show() 重新布局，冷启动多花约 100ms。
    try:
        config = ConfigManager()
        config.set("launcher_window_width", window.width(), auto_save=False)
        config.set("launcher_window_height", window.height(), auto_save=False)
        config.save()
    except Exception:
        pass
    window.show()
    activation_state['window'] = window
    if activation_server is not None and activation_server.consume_pending():
        QTimer.singleShot(0, activate_existing_window)
    if rename_recovery_error:
        try:
            from ui.widgets.toast import show_toast
            QTimer.singleShot(
                2500,
                lambda: show_toast(
                    rename_recovery_error, 'warning', window, 8000
                ),
            )
        except Exception:
            pass
    try:
        from core.update import consume_manual_update_notice
        manual_update_notice = consume_manual_update_notice()
        if manual_update_notice:
            from ui.widgets.toast import show_toast
            QTimer.singleShot(
                2500,
                lambda: show_toast(
                    manual_update_notice, 'warning', window, 8000
                ),
            )
    except Exception:
        pass
    try:
        if ConfigManager().recovered_bad_config:
            from ui.widgets.toast import show_toast
            QTimer.singleShot(
                1200,
                lambda: show_toast(
                    '配置文件已损坏，原文件已备份并恢复默认设置',
                    'warning', window, 6500,
                ),
            )
    except Exception:
        pass
    if leftover_old:
        def _show_old_backup_notice():
            try:
                if not any(Path(path).exists() for path in leftover_old):
                    return
                from ui.widgets.toast import show_toast
                show_toast(
                    '检测到旧版本回滚备份，将在下次启动清理',
                    'info', window, 6500,
                )
            except Exception:
                pass
        QTimer.singleShot(2500, _show_old_backup_notice)
    window.setWindowIcon(app.windowIcon())
    try:
        window.windowHandle().setIcon(app.windowIcon())
    except Exception:
        pass
    if ico_path.exists():
        from ui.runtime_icons import apply_windows_window_icon
        apply_windows_window_icon(window, ico_path)
        QTimer.singleShot(
            100, lambda: apply_windows_window_icon(window, ico_path)
        )
    window.setWindowState(Qt.WindowNoState)
    # 首帧绘制后再后台扫描 Java，避免启动卡顿

    exit_code = int(app.exec() or 0)
    # 打包版的 Qt/WMF 在 deleteLater/processEvents 阶段可能卡死并持有 GIL，
    # 连 threading.Timer 的 os._exit 兜底也无法执行，进程会残留成无窗口后台
    # 进程。正常关闭已在 closeEvent() 中保存窗口状态并释放单实例锁；这里只
    # 同步落盘配置/日志，然后立即结束进程，由系统回收窗口和媒体内核句柄。
    try:
        window._flush_exit_state()
    except Exception:
        pass
    try:
        logging.shutdown()
    except Exception:
        pass
    os._exit(exit_code)

if __name__ == "__main__":
    main()