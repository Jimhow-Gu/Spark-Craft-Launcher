import os
os.environ["QT_OPENGL"] = "software"   # 降低 OpenGL 栈冲突风险

import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from PySide6.QtCore import Qt, QObject, QEvent, QTimer
from PySide6.QtGui import QFont, QIcon

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.single_instance import acquire_single_instance, release_single_instance


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return base / relative

from core.paths import migrate_legacy_dirs, ensure_runtime_dirs
from core.logging_setup import setup_logging
setup_logging()

from ui.main_window import MainWindow
from core.config import ConfigManager
from core.java_scan import JavaScanService


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


def apply_windows_window_icon(window, icon_path):
    '''在窗口创建后显式写入任务栏大/小图标。'''

    if sys.platform != "win32":
        return
    try:
        import ctypes
        path = str(icon_path)
        if not path or not Path(path).is_file():
            return
        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040
        WM_SETICON = 0x0080
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        hicon = user32.LoadImageW(
            None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        if hicon:
            hwnd = int(window.winId())
            user32.SendMessageW.restype = ctypes.c_void_p
            user32.SendMessageW.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
            ]
            user32.SendMessageW(hwnd, WM_SETICON, 0, hicon)
            user32.SendMessageW(hwnd, WM_SETICON, 1, hicon)
            user32.SendMessageW(hwnd, WM_SETICON, 2, hicon)
            try:
                user32.SetClassLongPtrW.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                ]
                user32.SetClassLongPtrW.restype = ctypes.c_void_p
                user32.SetClassLongPtrW(hwnd, -14, hicon)
                user32.SetClassLongPtrW(hwnd, -34, hicon)
            except Exception:
                pass
            try:
                user32.GetWindowLongPtrW.restype = ctypes.c_longlong
                ex_style = user32.GetWindowLongPtrW(hwnd, -20)
                ex_style = (ex_style | 0x00040000) & ~0x00000080
                user32.SetWindowLongPtrW.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong,
                ]
                user32.SetWindowLongPtrW(hwnd, -20, ex_style)
                user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    0x0020 | 0x0001 | 0x0002 | 0x0004,
                )
            except Exception:
                pass
            window._taskbar_icon_handle = hicon
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

def main():
    set_taskbar_identity()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    from ui.widgets.checkbox_style import CheckBoxProxyStyle
    app.setStyle(CheckBoxProxyStyle())
    from ui.widgets.smooth_scroll import SmoothScroller
    app._smooth_scroller = SmoothScroller(app)
    app.installEventFilter(app._smooth_scroller)
    from ui.widgets.scrollbar_style import ScrollBarStyler
    app._scrollbar_styler = ScrollBarStyler(app)
    app.installEventFilter(app._scrollbar_styler)
    from ui.widgets.text_context_menu import TextContextMenuFilter
    app._text_context_menu = TextContextMenuFilter(app)
    app.installEventFilter(app._text_context_menu)
    app.aboutToQuit.connect(JavaScanService.instance().shutdown)
    app.aboutToQuit.connect(release_single_instance)
    app.setApplicationName("Spark Craft Launcher")
    app.setApplicationDisplayName("Spark Craft Launcher")
    from core.update import CURRENT_VERSION
    app.setApplicationVersion(CURRENT_VERSION)
    try:
        from core.mod_translations import translation_db
        translation_db.start_background_load()
    except Exception:
        pass
    app_icon = QIcon()
    ico_path = resource_path("ui/imgs/sparkler.ico")
    png_path = resource_path("ui/imgs/sparkler.png")
    if ico_path.exists():
        app_icon.addFile(str(ico_path))
    if png_path.exists():
        app_icon.addFile(str(png_path))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    from core.app_font import load_ui_fonts, ui_font
    load_ui_fonts()
    app.setFont(ui_font(10))

    if not acquire_single_instance():
        logging.getLogger("Startup").warning("检测到启动器已有实例，显示提示并退出当前实例")
        from ui.widgets.dark_dialog import DarkQuestionDialog
        DarkQuestionDialog(
            None, "提示",
            "检测到启动器已经在运行，本次启动将自动退出。",
            actions=[("确定", "ok", True)],
        ).exec()
        logging.getLogger("Startup").warning("已确认重复启动，当前实例退出")
        sys.exit(0)
    logging.getLogger("Startup").info("单实例锁已获取")

    # 更新时旧 exe 会被重命名为 .old；等旧进程退出后由后台线程清理。
    try:
        import threading
        from core.update import cleanup_old_executable
        threading.Thread(target=cleanup_old_executable, daemon=True).start()
    except Exception:
        pass

    # 只有首个实例才整理 SCL 运行目录，避免第二个实例移动正在使用的文件
    migrate_legacy_dirs()
    ensure_runtime_dirs()

    from ui.widgets.dark_dialog import install_dark_message_boxes
    install_dark_message_boxes()
    dialog_icon_filter = DialogIconFilter(app)
    app.installEventFilter(dialog_icon_filter)

    apply_packaged_defaults_once()
    init_game_dirs()

    window = MainWindow()
    window.setWindowIcon(app.windowIcon())
    if ico_path.exists():
        apply_windows_window_icon(window, ico_path)
    try:
        config = ConfigManager()
        config.set("launcher_window_width", window.width(), auto_save=False)
        config.set("launcher_window_height", window.height(), auto_save=False)
        config.save()
    except Exception:
        pass
    window.show()
    window.setWindowIcon(app.windowIcon())
    try:
        window.windowHandle().setIcon(app.windowIcon())
    except Exception:
        pass
    if ico_path.exists():
        apply_windows_window_icon(window, ico_path)
        QTimer.singleShot(
            100, lambda: apply_windows_window_icon(window, ico_path)
        )
    window.setWindowState(Qt.WindowNoState)
    # 首帧绘制后再后台扫描 Java，避免启动卡顿
    QTimer.singleShot(150, JavaScanService.instance().start)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()