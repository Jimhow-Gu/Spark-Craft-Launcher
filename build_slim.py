#!/usr/bin/env python3
"""Build the size-reduced Spark Craft Launcher one-file executable.

Requirements:
- Python 3.10 with PyInstaller 6.x and PySide6 (same environment as the normal build)

Media playback uses Windows Media Foundation, so no FFmpeg DLLs are bundled.
The build keeps all static Qt image-format plugins. UPX is disabled.
The exe icon is sparkler_neutral.ico; runtime taskbar icons are switched by theme.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "SparkCraftLauncher.slim.spec"
DIST = ROOT / "dist_slim"
WORK = ROOT / "build_slim_pyi"
BASELINE_BYTES = 36_662_822  # DLL39, the last FFmpeg-based build
IMAGE_PLUGINS = ("qgif.dll", "qico.dll", "qjpeg.dll", "qwebp.dll")
MSVC_NAMES = (
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll",
)
KEY_MARKERS = (
    "JDJhJDEwJFozbWlLbzY3SUYvTzEwYkIuQVJWM2VxOFBlUnpkU0lXOW1Id0xOSjRnMDVEQ0JySzJ5ZEhl",
    "$2a$10$Z3miKo67IF/O10bB.ARV3eq8PeRzdSIW9mHwLNJ4g05DCBrK2ydHe",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str):
    raise SystemExit("SLIM BUILD FAILED: " + message)


def build():
    DIST.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC),
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
    ]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def inspect():
    exe = DIST / "Spark Craft Launcher.exe"
    if not exe.is_file():
        fail(f"没有生成 {exe}")
    data = exe.read_bytes()
    if b"UPX!" in data:
        fail("检测到 UPX 标记；该构建不应使用 UPX")
    for marker in KEY_MARKERS:
        if marker.encode() in data:
            fail("构建中仍含旧 CurseForge Key")
    try:
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(exe))
        toc = archive.toc
        lowered = {str(name).replace("/", "\\").lower(): name for name in toc}
        if "pyside6\\plugins\\multimedia\\windowsmediaplugin.dll" not in lowered:
            fail("Windows Media Foundation 插件丢失")
        if "pyside6\\plugins\\multimedia\\ffmpegmediaplugin.dll" in lowered:
            fail("归档中仍包含 FFmpeg 多媒体插件")
        for name in lowered:
            base = name.rsplit("\\", 1)[-1]
            if base.startswith(("avcodec-", "avformat-", "avutil-",
                                "swscale-", "swresample-")):
                fail(f"归档中仍包含 FFmpeg DLL: {name}")
        for plugin in IMAGE_PLUGINS:
            name = f"pyside6\\plugins\\imageformats\\{plugin}"
            if name not in lowered:
                fail(f"静态图片解码插件丢失: {plugin}")
        for icon in (
            "sparkler_light.png", "sparkler_light.ico",
            "sparkler_dark.png", "sparkler_dark.ico",
        ):
            if f"ui\\imgs\\{icon}" not in lowered:
                fail(f"运行时主题图标丢失: {icon}")
        if any("tcl" in name or "tk8" in name for name in lowered):
            fail("归档中仍包含 Tcl/Tk 启动闪屏运行库")
        if "pyside6\\plugins\\tls\\qschannelbackend.dll" not in lowered:
            fail("Windows Schannel TLS 插件丢失")
        if "pyside6\\plugins\\tls\\qopensslbackend.dll" in lowered:
            fail("归档中仍包含未使用的 OpenSSL TLS 插件")
        counts = {}
        for name in lowered:
            base = name.rsplit("\\", 1)[-1]
            if base in MSVC_NAMES:
                counts[base] = counts.get(base, 0) + 1
        duplicate = next((name for name, count in counts.items() if count > 1), "")
        if duplicate:
            fail(f"VC 运行库未去重: {duplicate} x{counts[duplicate]}")
    except ImportError:
        fail("当前环境缺少 PyInstaller archive reader")
    return exe


def make_zip(exe: Path):
    out = DIST / "Spark.Craft.Launcher.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(exe, exe.name)
    return out


def main():
    build()
    exe = inspect()
    archive = make_zip(exe)
    size = exe.stat().st_size
    saved = BASELINE_BYTES - size
    print("\n=== SLIM BUILD OK ===")
    print(f"baseline DLL39: {BASELINE_BYTES:,} bytes")
    print(f"slim exe:       {size:,} bytes")
    print(f"saved:          {saved:,} bytes ({saved / BASELINE_BYTES * 100:.2f}%)")
    print(f"exe sha256:     {sha256(exe)}")
    print(f"zip:            {archive.stat().st_size:,} bytes")
    print(f"zip sha256:     {sha256(archive)}")
    print(f"output:         {exe}")


if __name__ == "__main__":
    main()
