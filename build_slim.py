#!/usr/bin/env python3
"""Build the size-reduced Spark Craft Launcher one-file executable.

Requirements:
- Python 3.10 with PyInstaller 6.x and PySide6 (same environment as the normal build)
- slim_ffmpeg/ with the five FFmpeg 7.1 DLLs and their SHA256SUMS

The build keeps all Qt image-format plugins and all FFmpeg playback decoders.
UPX is disabled.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "SparkCraftLauncher.slim.spec"
SLIM_FFMPEG = ROOT / "slim_ffmpeg"
DIST = ROOT / "dist_slim"
WORK = ROOT / "build_slim_pyi"
BASELINE_BYTES = 39_449_405  # DLL27, the last full-FFmpeg build
REPORTED_BASELINE_BYTES = 39_449_787
FFMPEG_FILES = (
    "avcodec-61.dll",
    "avformat-61.dll",
    "avutil-59.dll",
    "swscale-8.dll",
    "swresample-5.dll",
)
IMAGE_PLUGINS = ("qgif.dll", "qico.dll", "qjpeg.dll", "qwebp.dll")
KEY_MARKERS = (
    "JDJhJDEwJFozbWlLbzY3SUYvTzEwYkIuQVJWM2VxOFBlUnpkU0lXOW1Id0xOSjRnMDVEQ0JySzJ5ZEhl",
    "$2a$10$Z3miKo67IF/O10bB.ARV3eq8PeRzdSIW9mHwLNJ4g05DCBrK2ydHe",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str):
    raise SystemExit("SLIM BUILD FAILED: " + message)


def check_inputs():
    for name in FFMPEG_FILES:
        path = SLIM_FFMPEG / name
        if not path.is_file():
            fail(f"缺少 {path}")
    sums = SLIM_FFMPEG / "SHA256SUMS"
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            expected, name = parts[0], parts[-1].lstrip("*")
            path = SLIM_FFMPEG / name
            if path.is_file() and sha256(path) != expected:
                fail(f"文件校验失败: {name}")


def build():
    check_inputs()
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
        avcodec = toc.get("PySide6\\avcodec-61.dll")
        if not avcodec:
            fail("归档中没有精简 avcodec-61.dll")
        if int(avcodec[2]) != (SLIM_FFMPEG / "avcodec-61.dll").stat().st_size:
            fail("归档中的 avcodec 大小与 slim_ffmpeg 不一致")
        for plugin in IMAGE_PLUGINS:
            name = f"PySide6\\plugins\\imageformats\\{plugin}"
            if name not in toc:
                fail(f"静态图片解码插件丢失: {plugin}")
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
    print(f"baseline DLL27: {BASELINE_BYTES:,} bytes ({REPORTED_BASELINE_BYTES:,} reported)")
    print(f"slim exe:       {size:,} bytes")
    print(f"saved:          {saved:,} bytes ({saved / BASELINE_BYTES * 100:.2f}%)")
    print(f"exe sha256:     {sha256(exe)}")
    print(f"zip:            {archive.stat().st_size:,} bytes")
    print(f"zip sha256:     {sha256(archive)}")
    print(f"output:         {exe}")


if __name__ == "__main__":
    main()
