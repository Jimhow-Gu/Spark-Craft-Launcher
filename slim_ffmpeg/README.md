# Slim FFmpeg runtime (FFmpeg 7.1, LGPL)

Spark Craft Launcher only plays local background video/audio. It does not use FFmpeg
encoders, muxers, filters, devices, or program tools. These DLLs are a rebuild of
FFmpeg 7.1 with **all playback decoders/demuxers/parsers kept**, and only the unused
parts removed.

## Files

| File | Purpose |
|---|---|
| `avcodec-61.dll` | decoders (all kept) |
| `avformat-61.dll` | demuxers (all kept) |
| `avutil-59.dll` | common utilities |
| `swscale-8.dll` | pixel format conversion |
| `swresample-5.dll` | audio resampling |

These replace the five same-named DLLs bundled by PySide6. `ffmpegmediaplugin.dll`
is Qt's plugin and remains unchanged.

## Build configuration

Built from `ffmpeg-7.1.tar.xz` (SHA256 `40973d44970dbc83ef302b0609f2e74982be2d85916dd2ee7472d30678a7abe6`).

```sh
./configure \
  --prefix=... --target-os=mingw32 --arch=x86_64 \
  --enable-shared --disable-static \
  --disable-programs --disable-doc \
  --disable-avdevice --disable-avfilter --disable-postproc \
  --disable-encoders --disable-muxers --disable-devices \
  --disable-debug --disable-symver --disable-autodetect \
  --enable-w32threads --enable-d3d11va --enable-dxva2 \
  --enable-small --ln_s=cp \
  --extra-cflags='-Os' --extra-ldflags='-static-libgcc'

make -j4 SHELL=/bin/bash
```

Toolchain used:

- w64devkit 2.11.0 (GCC 16.2.0, GNU Make 4.4.1)
- NASM 2.16.03

The `--ln_s=cp` is required on Windows because creating symlinks in the build
directory is not available without extra privileges.

## Why this is safe for playback

- All FFmpeg **decoders** are retained.
- All FFmpeg **demuxers/parsers** are retained.
- Qt's multimedia plugin does not import `avfilter`, `avdevice`, or `postproc`.
- Launcher media is local playback only; it never encodes, remuxes, records, or
  streams media.

## Risks

- The DLLs are local, unsigned binaries; verify against the hashes in
  `slim_ffmpeg/SHA256SUMS`.
- All playback formats exercise the same decoder set as Qt's full FFmpeg, but a
  custom FFmpeg build is still a third-party binary change. Keep the original
  DLLs for rollback.
- FFmpeg is LGPL-2.1-or-later. The license text is included in this directory.
  Source is available from <https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz>; use the
  configuration above to reproduce the binaries.
