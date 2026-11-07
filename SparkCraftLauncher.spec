# -*- mode: python ; coding: utf-8 -*-
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    "PySide6.QtMultimedia",
    "PySide6.QtNetwork",
    "core.java_download",
    "core.forge_artifacts",
    "core.single_instance",
    "core.memory",
    "core.update",
    "core.seasonal",
    "lunarcalendar",
    "lunarcalendar.solarterm",
    "ui.widgets.java_download_dialog",
    "ui.widgets.update_dialog",
]
hiddenimports += collect_submodules("minecraft_launcher_lib")
# 官方构建的 CurseForge Key 放在本地忽略文件中；开源构建没有该文件时跳过。
if Path("core/_curseforge_key.py").is_file():
    hiddenimports.append("core._curseforge_key")

# 只打包界面实际使用的可选依赖，避免把 OpenSSL/cryptography 等带入安装包
excludes = [
    "OpenSSL",
    "cryptography",
    "urllib3.contrib.pyopenssl",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtVirtualKeyboard",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("ui/imgs", "ui/imgs"),
        # 仅打包实际使用的两个字重，且是 GB2312 + 界面文本子集。
        ("font/subset/HarmonyOS_Sans_SC_Black.ttf", "font"),
        ("font/subset/HarmonyOS_Sans_SC_Medium.ttf", "font"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# PySide6 hook 会收集一些未使用的 Qt 模块/插件；这里剔除，减小体积。
_drop_prefixes = (
    "qt6qml", "qt6quick", "qt6pdf", "qt6virtualkeyboard",
)
_drop_names = {
    "qtvirtualkeyboardplugin.dll", "qpdf.dll",
    # 软件 OpenGL 回退（约 20 MB）和未使用的平台/图片插件。
    # 注意：Python 3.10 的 _ssl/_hashlib 依赖 OpenSSL 1.1，必须保留；
    # 未被 Python/Qt 使用的 OpenSSL 3 运行库则移除。
    "opengl32sw.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll",
    "qdirect2d.dll", "qminimal.dll", "qoffscreen.dll",
    "qicns.dll", "qtga.dll", "qtiff.dll", "qwbmp.dll",
    "qcertonlybackend.dll",
    # 界面只使用 PNG/ICO/GIF，不包含 SVG；去掉未使用的 SVG 模块和插件。
    "qt6svg.dll", "qsvg.dll", "qsvgicon.dll",
    # QtMultimedia/Gui 均不依赖 OpenGL 模块，且启动器使用光栅绘制。
    "qt6opengl.dll",
    # 触摸屏插件未使用。
    "qtuiotouchplugin.dll",
}


def _keep_binary(entry):
    name = str(entry[0]).replace(chr(92), "/").lower()
    base = name.rsplit("/", 1)[-1]
    if "/translations/" in name:
        return False
    return not base.startswith(_drop_prefixes) and base not in _drop_names


a.binaries = [entry for entry in a.binaries if _keep_binary(entry)]
# translations 也可能被收集进 datas；启动器界面全部自带文本，不需要 Qt 翻译包。
# Loading.gif 是旧版备用图，实际使用 Loading_small.gif；打包时去掉大图。
_drop_datas = {"ui/imgs/loading.gif", "ui/imgs/alex_head.png"}
a.datas = [
    entry for entry in a.datas
    if "/translations/" not in str(entry[0]).replace(chr(92), "/").lower()
    and str(entry[0]).replace(chr(92), "/").lower() not in _drop_datas
]

# PyInstaller 遇到带 CFG 的 Qt DLL 会自动跳过 UPX；用户要求 Qt DLL 也压缩，
# 这里先把这些 DLL 复制到临时目录并用 UPX --force 压缩，再交给打包器。
_force_upx_names = {
    # Qt 核心 DLL 带 CFG，PyInstaller 默认跳过；只强制压缩这些已验证可用的核心库。
    # 平台/多媒体插件（qwindows、ffmpegmediaplugin 等）压缩后会报
    # “no Qt platform plugin could be initialized”，无论是否保留 load config 都不能压。
    "qt6gui.dll", "qt6widgets.dll", "qt6multimedia.dll",
    "qt6svg.dll", "qt6opengl.dll",
}
_upx = shutil.which("upx")
if _upx:
    _upx_cache = Path(tempfile.mkdtemp(prefix="scl-upx-"))
    for _index, (_dest, _src, _typ) in enumerate(list(a.binaries)):
        _base = os.path.basename(str(_src)).lower()
        if _base not in _force_upx_names:
            continue
        _target = _upx_cache / _base
        try:
            shutil.copy2(_src, _target)
            _result = subprocess.run(
                [_upx, "--force", "--lzma", "--strip-loadconf", "-q", str(_target)],
                capture_output=True,
                text=True,
            )
            if _result.returncode == 0:
                a.binaries[_index] = (_dest, str(_target), _typ)
        except OSError:
            continue

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Spark Craft Launcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 用户要求压缩 Qt/FFmpeg 等 DLL；Python/VC 运行时保留原样以提高稳定性。
    upx=True,
    upx_exclude=[
        "python310.dll", "python3.dll",
        "VCRUNTIME140.dll", "VCRUNTIME140_1.dll", "MSVCP140.dll",
        "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Multimedia.dll",
        "Qt6Svg.dll", "Qt6OpenGL.dll",
        "qwindows.dll", "ffmpegmediaplugin.dll", "windowsmediaplugin.dll",
        "qgif.dll", "qjpeg.dll", "qico.dll", "qwebp.dll", "qsvg.dll",
        "qsvgicon.dll", "qmodernwindowsstyle.dll", "qtuiotouchplugin.dll",
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="ui/imgs/sparkler.ico",
)
