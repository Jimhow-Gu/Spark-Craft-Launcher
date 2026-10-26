# -*- mode: python ; coding: utf-8 -*-
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
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("ui/imgs", "ui/imgs"),
        ("font/HarmonyOS_Sans_SC_Regular.ttf", "font"),
        ("font/HarmonyOS_Sans_SC_Bold.ttf", "font"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# PySide6 hook 会收集一些未使用的 Qt 模块 DLL；这里剔除，减小体积
_drop_prefixes = (
    "qt6qml", "qt6quick", "qt6pdf", "qt6virtualkeyboard",
)
_drop_names = {"qtvirtualkeyboardplugin.dll", "qpdf.dll"}


def _keep_binary(entry):
    name = str(entry[0]).replace(chr(92), "/").lower()
    base = name.rsplit("/", 1)[-1]
    return not base.startswith(_drop_prefixes) and base not in _drop_names


a.binaries = [entry for entry in a.binaries if _keep_binary(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SparkCraftLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="ui/imgs/sparkler.ico",
)
