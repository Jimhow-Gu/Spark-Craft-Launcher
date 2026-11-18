# Spark Craft Launcher V1.11.4 DLL40 构建说明

## 结论

DLL40 已完成构建与冒烟测试。这一版把 Qt 自带的 FFmpeg 栈替换为 Windows
Media Foundation，移除 PyInstaller 的 Tcl/Tk 闪屏和启动初始化页，直接进入
主窗口；同时加入浅色、深色、中性三套图标，并完成窗口圆角/启动渐显/动画刷新率修复。

| 产物 | 大小 | SHA256 |
|---|---:|---|
| DLL40 正式 onefile | 29,882,773 字节 | `89c75da5a249e5a34d31e18fb1f46630a8bd9a3097b67fff7fa13ed3188895e8` |
| DLL40 正式 zip | 29,586,183 字节 | `4eda92b698f76dd51e13cfa5ace2066cd081a568bcd777775c722b0bd558aabe` |
| DLL39 onefile（历史基线） | 36,662,822 字节 | `289383100f2209b5d2105e1aa25d5f5e76c80ac5a0e24cf4bc2d66379f73455c` |

- 构建号：`SCLREL1.11.4.R20261118.RP1.DLL40`
- DLL40 起：WMF 媒体后端、无 Tcl/Tk 闪屏、浅/深/中性三套图标、窗口四角填充式 1px 边框、
  启动不再整窗透明渐显；雪花/烟花最高 120 FPS，加载动画最高 60 FPS。
- 构建入口：`python build_slim.py`（当前正式发布）；`python build_onedir.py` 仅保留内部/回滚构建
- 安装包默认目录：`%LOCALAPPDATA%\Programs\SparkCraftLauncher`
- 不使用 UPX，不删除静态图片解码插件，不改变 UI 布局与业务功能。

## DLL40 更新

- 媒体后端从 Qt FFmpeg 切换到 Windows Media Foundation，移除 FFmpeg 运行库和插件；Windows N/KN 缺少 Media Feature Pack 时媒体初始化失败会被捕获。
- 移除 PyInstaller Tcl/Tk 闪屏和启动初始化页，窗口直接显示；不再使用整窗 `setWindowOpacity` 渐显，修复启动时透出其他应用文字和 DWM 合成卡顿。
- 新增浅色、深色、中性三套图标；exe/安装器使用中性版，运行中任务栏/Alt-Tab/关于页随主题切换，HICON 替换时释放旧句柄。
- 左上角 BrandLogo 恢复透明火花素材；窗口四角改为 outer 14/inner 13 的填充式 1px 圆角环，修正 border overlay 几何和窗口 mask 半像素裁切。
- 雪花/烟花按屏幕刷新率动态限制到最高 120 FPS，加载动画最高 60 FPS；最小化/隐藏停止动画定时器，支持跨屏 `screenChanged` 与 1 秒轮询。
- 保留 DLL39 的退出稳定性、.old 清理/重启删除、Win10 Fusion/Disabled 调色板、两位小数进度、更新窗口主题、设置间距、左键按钮等修复。
- 新增新闻手动刷新可见反馈：刷新中按钮文案、已是最新提示、失败立即提示、手动刷新后回顶。
- 未知大小下载使用 Content-Length 预检并每约 8MB 复查磁盘余量；最低保留 64MB。

## DLL36 / DLL37 / DLL38 / DLL39 更新

- 导出启动脚本改为 bat 调用启动器 `--launch-b64`，移除 PowerShell / `-EncodedCommand`，
  版本名和参数不进入 cmd。
- 内存配置清洗确保 `-Xms` 不大于 `-Xmx`；限速继续过滤 NaN。
- 正常退出改为 `deleteLater → processEvents → sys.exit`，`os._exit` 仅保留为超时兜底；
  更新替换 exe 的路径仍立即退出。
- 整合包解压移除固定单文件/总大小/压缩比上限，仅保留 zip-slip 和磁盘余量检查
  （含 10% 预留）；manifest 嵌套过深转为可读错误。
- JVM 参数解析支持单双引号；导出脚本继续兼容 authlib-injector 和本地代理参数。
- 关于页 Gubwon 头像改用账户页同款 3D 皮肤渲染，内置完整皮肤；mc-heads 403 时
  自动回退 minotar/mineskin，且只有全部来源失败才记录警告。
- Mod 详情页来源按钮按项目实际来源显示；只有 Mod 显示 MC 百科，点击后解析
  `class/{id}.html` 直接打开详情页（参考 PCL 的 WikiId 思路）。
- 设置页 TAB 标签栏与内容之间的空白带、折叠列表展开/收起残影已处理。
- 当前版本高于已发布正式版时，显示白色可点击提示，引导下载最新正式版。
- 更新附件选择跳过 `.bak`/`.old` 备份，避免自动更新下载到旧构建。
- DLL37 按指定值恢复设置页顶部间距为 24px，设置分组顶部内边距改为 0，调整卡片标题与内容的首行布局。
- DLL38 按 PCL CE 的检测顺序补齐 MCBBS、HMCL modpack.json、MultiMC 无 instance.cfg、manifest addons 等格式；数据包下载不再检测/要求世界，默认保存到游戏目录并支持选择世界 datapacks；无已安装游戏时允许下载整合包；Mod 拖入支持 .disabled/.old 恢复为 jar。
- DLL39 游戏设置 TAB 增加 24px 顶部间距；增加按钮只响应左键；关闭时不再同步 setSource，watchdog 提前到媒体清理之前；旧 exe 清理增加启动/退出快速尝试与更密的首次重试；下载进度统一显示两位小数；更新窗口适配深浅主题，自动重启时按钮显示“正在重启...”并禁用；Win10 使用 Fusion 基础样式并补齐 Disabled 调色板。

## 启动性能

在 Windows 11、当前开发机的实测：

| 场景 | 窗口可见 | 外部消息可响应 |
|---|---:|---:|
| 安装后首次启动（安装器已预热） | 1.10s | 1.29s |
| 后续启动 | 1.03–1.06s | 1.03–1.24s |
| DLL40 onefile 冷启动（当前开发机实测） | 约 1.5s | - |
| DLL40 `--prewarm` | 约 1.47s | - |

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

## 体积构成与媒体后端

DLL39 onefile 为 36,662,822 字节；DLL40 为 29,882,773 字节，减少
6,780,049 字节（18.49%），落在 28–30 MB 目标区间。主要变化：

- 移除精简 FFmpeg 运行库（约 4.5 MB 压缩后）；
- 移除 PyInstaller Tcl/Tk 闪屏（约 1.5 MB 压缩后）；
- 保留 WMF 插件 `windowsmediaplugin.dll`；
- 保留 `qgif/qico/qjpeg/qwebp` 四个静态图片插件；
- 字体仍只打包 HarmonyOS Sans SC Medium/Black 两个字重；
- 重复的 VC 运行时按文件名只保留一份。

媒体播放改走 Windows Media Foundation：

- MP4/H.264/AAC、MP3、WAV、M4A 等 Windows 自带格式正常播放；
- WebM/OGG/Opus 等格式在未安装系统解码器时可能无法播放，播放器会报错但不会崩溃；
- Windows N/KN 需要安装 Media Feature Pack；未安装时背景视频/音乐初始化失败会被捕获，
  不影响启动器其他功能；
- 必须在任何 `PySide6.QtMultimedia` 导入前设置 `QT_MEDIA_BACKEND=windows`。

## 图标

- `sparkler_neutral.ico`：编译进 PE，用于 exe 文件和固定到任务栏的快捷方式；
- `sparkler_light.*` / `sparkler_dark.*`：运行中任务栏、标题栏、Alt-Tab、BrandLogo、关于页；
- 运行时切换只替换 QIcon/HICON，不修改 exe、.lnk 或 Windows 图标缓存；
- HICON 每次切换后销毁旧句柄，任务栏刷新使用 `WM_SETICON + SetClassLongPtrW + SetWindowPos(SWP_FRAMECHANGED)`。

## 启动流程

不显示额外的启动闪屏或初始化页。窗口创建后直接构建主页，目录迁移和游戏目录扫描
仍在主窗口构造前完成；侧栏左上角 BrandLogo 继续使用原来的透明火花素材，不套用
圆角底板图标。

## 构建

环境：Python 3.10、PySide6 6.11.1、PyInstaller 6.21、Inno Setup 6.7.4。

```powershell
cd D:\PMGL
python build_onedir.py                  # onedir + 更新 zip + 安装包（内部/回滚）
python build_slim.py                    # onefile 便携版 + zip
```

`build_onedir.py` 会：

1. 用 `SparkCraftLauncher.slim_onedir.spec` 构建；
2. 校验 exe、`_internal/`、静态图片插件、WMF 插件、主题图标、无 FFmpeg、无 UPX、
   无旧 CurseForge Key；
3. 生成 `dist_installer/SparkCraftLauncher-V{版本}-onedir.zip`；
4. 调用 Inno Setup 生成安装包；
5. 输出体积和 SHA256。

`build_slim.py` 会校验 onefile 中的 WMF 插件、图片插件、主题图标、VC 运行时去重、
无 FFmpeg/Tcl-Tk、无 UPX 和旧 Key。

`INNO_SETUP_ISCC` 可指定 `ISCC.exe` 路径；找不到时只跳过安装包生成。
安装包语言文件为 `installer/ChineseSimplified.isl`。

## 回滚

用户数据不需要回滚：

1. 重新运行旧版安装包覆盖安装，或把旧版 onedir 目录中的 exe 与 `_internal/` 替换回去；
2. 保留 `SCL/` 不动；
3. 若自动更新失败，更新器会恢复 `.old` 备份；启动后也会清理残留备份。

## 风险说明

- 未做 Authenticode 签名，SmartScreen/杀软提示不会因瘦身或 onedir 消失。
- WMF 的可用格式取决于 Windows 版本和系统解码器；Windows N/KN 未安装 Media
  Feature Pack 时媒体功能不可用，但启动器其余功能不受影响。
- 安装器预热只加载模块，不创建窗口、不读取账号配置；若在异常环境被安全软件拦截，
  仍可手动运行启动器，功能不受影响。
