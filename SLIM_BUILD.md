# Spark Craft Launcher V1.11.4 DLL33 构建说明

## 结论

当前正式发布恢复到 **V1.11.4 + DLL33 onefile**。1.11.5–1.11.7 的 onedir/安装包
发布已转为草稿并撤回，以下保留内部构建与回滚说明。

| 产物 | 大小 | SHA256 |
|---|---:|---|
| 正式 onefile 便携 exe | 36,644,986 字节 | `3bbd91b0cd0bb049df1069a21ee908b86f8894fb95e8820fa94a2eac4e6b2b1b` |
| 正式 onefile 便携 zip | 36,322,292 字节 | `aec77719d856c859f5e0f27798b1b8711ad13577c45183eda2139912f4085117` |

- 构建号：`SCLREL1.11.4.R20261115.RP1.DLL33`
- 构建入口：`python build_slim.py`（当前正式发布）；`python build_onedir.py` 仅保留内部/回滚构建
- 安装包默认目录：`%LOCALAPPDATA%\Programs\SparkCraftLauncher`
- 不使用 UPX，不删除静态图片解码插件，不改变 UI 布局与业务功能。

## 启动性能

在 Windows 11、当前开发机的实测：

| 场景 | 窗口可见 | 外部消息可响应 |
|---|---:|---:|
| 安装后首次启动（安装器已预热） | 1.10s | 1.29s |
| 后续启动 | 1.03–1.06s | 1.03–1.24s |
| onefile 便携版（旧兼容包） | 1.69–1.95s | - |

启动优化做法：

1. 主页、版本选择器、账号刷新、背景音乐、非当前页面全部延迟到窗口显示后创建；
2. 侧栏和账户页共用 `ui/widgets/avatar_label.py`，不再因侧栏头像导入整个账号页；
3. 安装器在文件写入后执行一次 `--prewarm`：只加载模块和 Qt，不建窗口、不读账号配置，减少 Windows 首次执行扫描的影响；
4. 无边框窗口的原生图标在 `show()` 后设置，避免提前调用 `winId()` 创建两次原生窗口；
5. 保留启动淡入、页面切换动画、SCL 数据结构、所有页面和下载/启动逻辑。

## V1.11.7 稳定性与头像修复

- 修复模组/整合包等浏览页反复切换时 `SearchWorker` 被 `QThread.terminate()` 强制终止、
  `finished` 不触发导致线程对象和请求上下文线性累积的问题；现在只协作取消，
  线程自然收尾。
- 快速切页回归：60 组往返后 QThread 稳定为 0，RSS 不再线性增长；打包版模组页
  85 次往返后线程约 21–27、句柄约 1390，保持稳定。
- `.old` 清理改为后台多轮重试（0/5/15/30/60/120 秒），并在日志中记录仍被占用的
  文件；杀软/旧进程释放句柄后会自动删除。
- 这项修复同时解决了 Windows 资源耗尽事件中启动器进程占用约 38GB 提交内存的问题。
- 离线随机默认皮肤现在按玩家名固化：同一账号首次抽到 Steve/Alex 后写入
  `offline_default_skins`，之后每次进入账号页/启动游戏保持一致。
- 皮肤缓存加入 PNG 尺寸校验；损坏缓存会被删除并重新拉取。
- 修复 96x96 网络头图被写进 `microsoft_avatar_*.png` 后长期平铺的问题：只有
  64 基准完整皮肤才进入 3D 缓存，头图另存 `*_head.png`；命中缓存也会先校验尺寸。
- 头像网络请求增加代际号：`set_image_url`/`set_skin_file` 会取消上一轮请求，晚到的
  2D 头图不会再把已显示的 3D 皮肤覆盖回平面。
- 正版/第三方皮肤 worker 增加 generation、账号 key 和 refresh token 校验；
  切号或重复刷新后旧结果直接丢弃。
- 账户页按账号保留最近一次 3D/2D 头像源；切回同一账号不再重新走
  “史蒂夫 → 2D”，只更新文字并在后台静默刷新。
- 第三方浏览页缓存命中也会后台校验更新；切号/登录会绕过 30 秒冷却。
- 正版档案超时降至 12 秒，纹理下载单独 5 秒；手动刷新不再被旧 worker 挡住。
- 正版/第三方完整皮肤刷新成功后优先覆盖旧缓存，侧栏和账户页统一使用 3D 源。
- 正版皮肤资料刷新失败会做 2 次退避重试；切换登录方式后旧线程结果不会再覆盖
  当前账号头像。
- 正版/第三方网络头像都失败时显示通用人物占位，不再用史蒂夫冒充“加载成功”；
  离线 premium 下载失败则回退本地默认皮肤的 3D 渲染，并自动重试一次。

## 安装/升级/卸载与 SCL

- `SCL/` 是用户数据目录（配置、账号、皮肤、缓存、日志、更新记录），安装、覆盖升级、
  自动更新、卸载都不会删除它。
- 卸载只清理 `_internal/`、启动器 exe、旧 `.old` 备份和安装清单中的快捷方式；
  `SCL/`、`.minecraft/` 会保留。
- 覆盖升级先清理旧 `_internal/`，避免旧 DLL/Qt 插件与新版本混用。
- 安装器检测到启动器正在运行时会要求先退出，不会替换正在使用的文件。
- 安装器使用固定的 Inno `AppId`，后续版本可原位覆盖升级。

## onedir 自动更新

下载完成后根据当前安装形态选择附件：

- onedir 安装：优先 `*-onedir.zip`；
- onefile 便携版：优先非安装器 exe，再回退 zip。

onedir 更新流程：

1. 安全解压到 `SCL/update/onedir_new`（拒绝 zip slip、`SCL/` 用户数据和异常体积）；
2. 校验启动器 exe 的 `MZ` 头和 `_internal/` 内容；
3. PowerShell 更新器等待旧进程退出；
4. 把当前 `_internal`、exe 备份为 `.old`，复制新目录；
5. 启动新版本；成功则清理备份，失败则恢复旧版本；
6. 下次启动清理残留 `_internal.old`。

更新器只在 `SCL/update` 下工作，不会移动或覆盖 `SCL/` 用户数据。

## 体积构成与 FFmpeg

未使用 UPX 的 DLL27 onefile 基线为 39,449,405 字节。V1.11.7 onefile 为
36,645,429 字节，减少 2,803,976 字节（7.11%）。

主要占用：

- Qt6/PySide6：约 16MB（压缩后）
- 精简 FFmpeg：约 4.5MB（压缩后）
- 字体：约 2.47MB（压缩后）
- Python 运行时/PYZ：约 5MB（压缩后）

基于 FFmpeg 7.1 自定义构建，保留：

- 全部 decoders
- 全部 demuxers / parsers
- `avcodec`、`avformat`、`avutil`、`swscale`、`swresample`
- x86 ASM、D3D11VA/DXVA2 硬件解码路径

关闭：

- encoders、muxers、avfilter、avdevice、programs、docs
- `--enable-small -Os` 优化体积，不改变解码器集合

静态图片插件 `qgif/qico/qjpeg/qwebp` 全部保留；`ffmpegmediaplugin.dll` 原样保留。
源码、配置、哈希和 LGPL-2.1-or-later 许可见 `slim_ffmpeg/README.md` 和
`slim_ffmpeg/COPYING.LGPLv2.1`。

## 构建

环境：Python 3.10、PySide6 6.11.1、PyInstaller 6.21、Inno Setup 6.7.4。

```powershell
cd D:\PMGL
python build_onedir.py                  # onedir + 更新 zip + 安装包
python build_slim.py                    # onefile 便携版 + zip
```

`build_onedir.py` 会：

1. 校验 `slim_ffmpeg/` 哈希；
2. 用 `SparkCraftLauncher.slim_onedir.spec` 构建；
3. 校验 exe、`_internal/`、静态图片插件、无 UPX、无旧 CurseForge Key；
4. 生成 `dist_installer/SparkCraftLauncher-V1.11.7-onedir.zip`；
5. 调用 Inno Setup 生成 `dist_installer/V1.11.7-Setup-SparkCraftLauncher.exe`；
6. 输出体积和 SHA256。

`INNO_SETUP_ISCC` 可指定 `ISCC.exe` 路径；找不到时只跳过安装包生成。
安装包语言文件为 `installer/ChineseSimplified.isl`。

## 回滚

用户数据不需要回滚：

1. 重新运行旧版安装包覆盖安装，或把旧版 onedir 目录中的 exe 与 `_internal/` 替换回去；
2. 保留 `SCL/` 不动；
3. 若自动更新失败，更新器会恢复 `.old` 备份；启动后也会清理残留备份。

## 风险说明

- 未做 Authenticode 签名，SmartScreen/杀软提示不会因瘦身或 onedir 消失。
- FFmpeg 为本地重新编译的未签名二进制；常见播放格式已实测，但极端编码覆盖范围
  不等同于 Qt 官方全量构建的逐字节保证。
- 移除了 FFmpeg 编码/复用/滤镜/设备模块；当前启动器只做本地播放。未来新增录制/
  转码功能时需恢复对应模块。
- 安装器预热只加载模块，不创建窗口、不读取账号配置；若在异常环境被安全软件拦截，
  仍可手动运行启动器，功能不受影响。
