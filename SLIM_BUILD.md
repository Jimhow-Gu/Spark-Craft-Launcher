# Spark Craft Launcher 瘦身构建（DLL27 slim）

## 结果

| 项目 | 大小 |
|---|---:|
| 原 DLL27 exe | 39,449,405 字节 |
| 报告中的 v2 修复版 | 39,449,787 字节 |
| slim exe | **36,631,668 字节** |
| 相对 DLL27 节省 | **2,817,737 字节（7.14%）** |
| slim zip | 36,310,002 字节 |

- exe SHA256：`4e3b4ed5185c4185b34e06f11b03655f45e4d28ee77a1d7e5c379f340481a536`
- zip SHA256：`117202a2a92880ef48faf22f3e24ecb1710b65384bea38f3a1b65e54467b7ec6`
- 构建号：`SCLREL1.11.4.R20261112.RP1.DLL28`
- 可替换文件：`dist_slim/Spark Craft Launcher.exe`
- 构建入口：`python build_slim.py`

## 体积构成

未使用 UPX 的 DLL27 基线为 39,450,825 字节，和 UPX 版几乎一样；原因是 PyInstaller onefile 本身已用 zlib 压缩，对 Qt/FFmpeg 再套 UPX 几乎没有收益，却会带来杀软误报和签名问题。因此本方案完全关闭 UPX。

主要占用（解包后）：

- Qt6/PySide6：约 16MB 压缩
- 全量 FFmpeg：约 8.2MB 压缩（`avcodec` 单项约 6.0MB）
- 字体：约 2.47MB 压缩
- Python 运行时/PYZ：约 5MB 压缩

## 采用的方案

### 1. FFmpeg 只裁掉“本项目不用的模块”，保留全部播放解码器

基于 FFmpeg 7.1 重新编译，保留：

- 全部 decoders
- 全部 demuxers / parsers
- `avcodec`、`avformat`、`avutil`、`swscale`、`swresample`
- x86 ASM、D3D11VA/DXVA2 硬件解码路径

关闭：

- encoders（启动器不编码）
- muxers（启动器不封装/转码）
- avfilter（Qt 插件不链接 avfilter）
- avdevice / devices（不采集设备）
- programs / docs

同时使用 `--enable-small` 和 `-Os`，只改变编译优化策略，不改变解码器集合。`ffmpegmediaplugin.dll` 由 Qt 提供，原样保留。

### 2. PyInstaller `optimize=2`

移除字节码 docstring/assert 等运行时不需要的内容，PYZ 从约 3.10MB 降到约 2.63MB。

### 3. 完全关闭 UPX

`SparkCraftLauncher.slim.spec` 中 `upx=False`，没有 UPX 自定义压缩块。静态图片解码插件 `qgif/qico/qjpeg/qwebp` 全部保留。

### 4. 不修改 UI / 业务代码

没有修改任何页面、样式、布局、功能逻辑；只新增 slim spec、精简 FFmpeg DLL 和构建脚本。

## 实测验证清单

- [x] slim exe 正常启动，真实窗口出现，`Responding=True`。
- [x] 运行时确认从 `_MEI.../PySide6/avcodec-61.dll` 加载精简 DLL。
- [x] HEVC/AAC 1080p 背景视频可播放；5 秒内 145 帧，原版约 147 帧（差异在采样噪声范围）。
- [x] H.264/AAC、MP3、WAV 均能进入 `LoadedMedia`；MP4/MP3/WAV 元数据和时长正确。
- [x] `qgif.dll`、`qico.dll`、`qjpeg.dll`、`qwebp.dll` 仍在包内。
- [x] 构建产物无 `UPX!` 标记。
- [x] 构建产物不含旧 CurseForge Key。
- [x] 打包 exe 关闭后进程正常退出。
- [x] 未改变配置目录、SCL 数据结构或账号数据。

## 构建

```bash
cd D:/PMGL
python build_slim.py
```

要求：

- Python 3.10 + PyInstaller 6.x + PySide6 6.11.1
- `slim_ffmpeg/` 中五个 DLL 与 `SHA256SUMS` 一致
- 不需要安装 UPX

构建脚本会：

1. 校验 `slim_ffmpeg` 文件哈希；
2. 用 `SparkCraftLauncher.slim.spec` 构建；
3. 校验归档中的精简 FFmpeg、静态图片插件、无 UPX、无旧 Key；
4. 生成 zip；
5. 输出体积对比和 SHA256。

FFmpeg DLL 的来源、配置、哈希和 LGPL 许可见 `slim_ffmpeg/README.md`。

## 回滚方式

保留原版文件即可随时回滚，不需要动 SCL 用户数据：

1. 用原 DLL27 的 `Spark Craft Launcher.exe` 覆盖 `dist_slim/Spark Craft Launcher.exe`（或直接替换已部署 exe）。
2. 退出并重新启动启动器。
3. 如果只回滚 FFmpeg：用安装目录中 PySide6 原始的五个 DLL 替换 `slim_ffmpeg` 中同名文件，再运行 `python build_slim.py`。
4. 若已上传为 Release 资产，把原 DLL27 exe/zip 重新上传覆盖即可。

## 风险说明

- **FFmpeg DLL 是本地重新编译的未签名二进制**：已实测常见播放格式，但极端/罕见编码的覆盖范围取决于 FFmpeg 解码器，不等同于 Qt 官方全量构建的逐字节保证。
- 移除了 FFmpeg 编码器/复用器/滤镜/设备模块。当前启动器只做本地播放，不使用 `QMediaRecorder`、转码或采集；未来若新增录制/转码功能，需要恢复对应模块。
- `--enable-small/-Os` 可能让极端高码率视频的软解略慢；1080p HEVC 实测帧数与完整版基本一致。
- 仍未做 Authenticode 签名，SmartScreen/杀软提示不会因为瘦身消失。
- 分发这些 FFmpeg DLL 时需遵守 LGPL-2.1-or-later；`slim_ffmpeg/COPYING.LGPLv2.1` 已附，源码与构建配置已给出。
