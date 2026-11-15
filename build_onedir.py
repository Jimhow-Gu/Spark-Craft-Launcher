#!/usr/bin/env python3
"""Build the PyInstaller onedir package, zip update asset and Inno installer.

Usage:
    python build_onedir.py
    python build_onedir.py --skip-installer

The output layout is:
    dist_onedir/SparkCraftLauncher/                 runnable directory
    dist_installer/SparkCraftLauncher-V*-onedir.zip update asset
    dist_installer/V*-Setup-SparkCraftLauncher.exe  first-install setup
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "SparkCraftLauncher.slim_onedir.spec"
DIST = ROOT / "dist_onedir"
WORK = ROOT / "build_onedir"
COLLECT = DIST / "SparkCraftLauncher"
EXE = COLLECT / "Spark Craft Launcher.exe"
INSTALLER_DIR = ROOT / "dist_installer"
ISS = ROOT / "installer" / "SparkCraftLauncher.iss"
SLIM_FFMPEG = ROOT / "slim_ffmpeg"
FFMPEG_FILES = (
    "avcodec-61.dll",
    "avformat-61.dll",
    "avutil-59.dll",
    "swscale-8.dll",
    "swresample-5.dll",
)
IMAGE_PLUGINS = ("qgif.dll", "qico.dll", "qjpeg.dll", "qwebp.dll")
# 复用 onefile 构建脚本中的旧 Key 标记检查，避免在仓库里再复制一份。
try:
    from build_slim import KEY_MARKERS
except Exception:
    KEY_MARKERS = ()


def version():
    text = (ROOT / "core" / "update.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("CURRENT_VERSION = "):
            return line.split("'", 2)[1]
    raise SystemExit("cannot read CURRENT_VERSION from core/update.py")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fail(message):
    raise SystemExit("ONEDIR BUILD FAILED: " + message)


def check_inputs():
    for name in FFMPEG_FILES:
        if not (SLIM_FFMPEG / name).is_file():
            fail(f"missing {SLIM_FFMPEG / name}")
    sums = SLIM_FFMPEG / "SHA256SUMS"
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            expected, name = parts[0], parts[-1].lstrip("*")
            path = SLIM_FFMPEG / name
            if path.is_file() and sha256(path) != expected:
                fail(f"FFmpeg checksum mismatch: {name}")


def build():
    check_inputs()
    cmd = [
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--noconfirm", "--clean",
        "--distpath", str(DIST), "--workpath", str(WORK),
    ]
    print("running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def inspect():
    if not EXE.is_file() or not (COLLECT / "_internal").is_dir():
        fail("onedir output is incomplete")
    data = EXE.read_bytes()
    if b"UPX!" in data:
        fail("UPX marker found in launcher exe")
    for marker in KEY_MARKERS:
        if marker.encode() in data:
            fail("old CurseForge key found in launcher exe")
    image_dir = COLLECT / "_internal" / "PySide6" / "plugins" / "imageformats"
    for name in IMAGE_PLUGINS:
        if not (image_dir / name).is_file():
            fail(f"image decoder plugin missing: {name}")
    avcodec = COLLECT / "_internal" / "PySide6" / "avcodec-61.dll"
    if not avcodec.is_file():
        fail("slim avcodec-61.dll missing")
    if avcodec.stat().st_size != (SLIM_FFMPEG / "avcodec-61.dll").stat().st_size:
        fail("slim FFmpeg in output differs from slim_ffmpeg/")
    return EXE


def make_update_zip(ver: str):
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    out = INSTALLER_DIR / f"SparkCraftLauncher-V{ver}-onedir.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in COLLECT.rglob("*"):
            if path.is_file():
                archive.write(path, Path(COLLECT.name) / path.relative_to(COLLECT))
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
        required = {
            f"{COLLECT.name}/Spark Craft Launcher.exe",
            f"{COLLECT.name}/_internal/python310.dll",
        }
        if not required.issubset(names):
            fail("update zip does not contain the complete onedir layout")
    return out


def find_iscc():
    env = os.environ.get("INNO_SETUP_ISCC", "").strip()
    candidates = [
        Path(env) if env else None,
        Path(r"D:\Inno Setup 6\ISCC.exe"),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Inno Setup 6" / "ISCC.exe",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def build_installer():
    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup 6 not found; skipped installer. Set INNO_SETUP_ISCC to compile it.")
        return None
    print("running:", iscc, ISS, flush=True)
    subprocess.run([str(iscc), str(ISS)], cwd=str(ROOT), check=True)
    out = INSTALLER_DIR / f"V{version()}-Setup-SparkCraftLauncher.exe"
    if not out.is_file():
        fail("installer was not generated")
    return out


def main():
    skip_installer = "--skip-installer" in sys.argv[1:]
    ver = version()
    build()
    exe = inspect()
    update_zip = make_update_zip(ver)
    installer = None if skip_installer else build_installer()
    print("\n=== ONEDIR BUILD OK ===")
    print(f"version:       {ver}")
    print(f"directory:     {COLLECT}")
    print(f"exe:           {exe.stat().st_size:,} bytes")
    print(f"exe sha256:    {sha256(exe)}")
    print(f"update zip:    {update_zip.stat().st_size:,} bytes")
    print(f"update sha256: {sha256(update_zip)}")
    if installer is not None:
        print(f"installer:     {installer.stat().st_size:,} bytes")
        print(f"setup sha256:  {sha256(installer)}")
    else:
        print("installer:     skipped")


if __name__ == "__main__":
    main()
