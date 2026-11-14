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
    "ui.widgets.java_download_dialog",
    "ui.widgets.update_dialog",
]
hiddenimports += collect_submodules("minecraft_launcher_lib")

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
    # 未直接使用的可选压缩/日期库；移除避免把 pytz 时区库、ephem 等打进包内。
    "lunarcalendar",
    "pytz",
    "ephem",
    "dateutil",
    "zstandard",
    "brotli",
    "brotlicffi",
    "_brotli",
    # 共享 CurseForge Key 会被 exe 提取，已改为用户自填；禁止再打包。
    "core._curseforge_key",
    "core.curseforge_builtin",
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
    optimize=2,
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
_drop_datas = {"ui/imgs/loading.gif"}
a.datas = [
    entry for entry in a.datas
    if "/translations/" not in str(entry[0]).replace(chr(92), "/").lower()
    and str(entry[0]).replace(chr(92), "/").lower() not in _drop_datas
]

# 用只移除编码器/复用器/滤镜、保留全部播放解码器的精简 FFmpeg 替换 Qt 全量包。
# 这样不改变播放格式支持，也不使用 UPX。
_slim_ffmpeg = Path("slim_ffmpeg")
_slim_libs = {
    "avcodec-61.dll": _slim_ffmpeg / "avcodec-61.dll",
    "avformat-61.dll": _slim_ffmpeg / "avformat-61.dll",
    "avutil-59.dll": _slim_ffmpeg / "avutil-59.dll",
    "swscale-8.dll": _slim_ffmpeg / "swscale-8.dll",
    "swresample-5.dll": _slim_ffmpeg / "swresample-5.dll",
}
for _name, _src in _slim_libs.items():
    if not _src.is_file():
        raise SystemExit(f"缺少精简 FFmpeg 文件: {_src}")
_slim_names = {name.lower() for name in _slim_libs}
a.binaries = [
    entry for entry in a.binaries
    if os.path.basename(str(entry[0])).lower() not in _slim_names
]
a.binaries.extend(
    (os.path.join("PySide6", name), str(src), "BINARY")
    for name, src in _slim_libs.items()
)

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
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="ui/imgs/sparkler.ico",
)
