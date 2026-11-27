# Spark Craft Launcher V1.11.4 DLL46 构建说明

## 结论

DLL46 已完成构建与冒烟测试。这一版完成版本类别下拉重写与自动收起修复、
社区资源筛选行统一、加载器失败/超时修复，并汇总 Java 要求、整合包资源、
新闻 Archive、列表动画和图形启动等本轮修复。

| 产物 | 大小 | SHA256 |
|---|---:|---|
| DLL46 正式 onefile | 30,016,589 字节 | `1e17b827d12cd85c2099b541145442d5c85029b6239b0c1d5c0c4eb3f5bb5fc9` |
| DLL46 正式 zip | 29,718,842 字节 | `c20f6d57e7a2fc03228bc6f70043960fe06a2352ca9940eecf69270a07209adc` |
| DLL45 onefile（历史基线） | 29,953,652 字节 | `22ad19940327c974be6bdfc46b826a0234fe2603ef159f77c569fb64d5e08bb1` |
| DLL45 zip（历史基线） | 29,655,714 字节 | `86d3af445057b128594900615a28445768fd5a88d150271c1ee42a9b47e00b27` |
| DLL44 onefile（历史基线） | 29,926,348 字节 | `f91f8787ce5c9f0d30fa5db2b564f04b535ecf63f1e02f393b20bbd747c7a48a` |
| DLL44 zip（历史基线） | 29,628,578 字节 | `f92bffbc904d0bb8daa17f5ccd3d322e2ba7563e674d0439bb053ab0c41582d1` |
| DLL43 onefile（历史基线） | 29,914,606 字节 | `34be026ce67cace7f7b581552b780565f54f9099503ad57430fea84c4d7c2fea` |
| DLL42 onefile（历史基线） | 29,906,369 字节 | `3b17c87469c330e52bd2c14b470d12c156c8bee78e820ccc41e7472c1529438d` |
| DLL41 onefile（历史基线） | 29,903,516 字节 | `89eed52c2d55dfe9ee33a921e155008ec251ec059935d7651b789f4abdc8c39a` |
| DLL40 onefile（历史基线） | 29,882,773 字节 | `89c75da5a249e5a34d31e18fb1f46630a8bd9a3097b67fff7fa13ed3188895e8` |
| DLL39 onefile（历史基线） | 36,662,822 字节 | `289383100f2209b5d2105e1aa25d5f5e76c80ac5a0e24cf4bc2d66379f73455c` |

- 构建号：`SCLREL1.11.4.R20261127.RP1.DLL46`
- DLL46 起：版本类别下拉改为自绘 Tool 弹层，杜绝自动收起；社区资源筛选统一为自绘下拉并移除“全部加载器”；Forge/NeoForge 增加 CurseForge/GitCode/本地安装器兜底，OptiFine 缩短探测并隐藏无版本项。
- DLL45 起：背景视频 WMF 后端与黑帧/解码失败诊断、26.3 多源元数据和逐源日志、
  版本源真正切换、Mod 递归/JiJ 统计、现有实例整合包备份合并、新闻 Archive、
  统一错误/重试状态、下拉和右键菜单圆角。
- DLL44 起：`.old` 每轮删前清进程、任务栏更新器父进程路径校验、背景黑帧回退、
  快照版本精确匹配、整合包现有实例安装、必需/可选依赖分组。
- DLL43 起：启动/点击性能优化、原生 Windows 任务栏进度、关闭媒体释放、启动修复单次链路、
  Capsule 版本筛选、圆角返回按钮、版本列表标签/行高/省略与文件选择状态色。
- DLL42 起：整数内存参数、PCL 风格启动脚本、BMCL/OpenBMC 安装器回退、
  installing/updated/failed 更新回执、空暂存文件默认拒绝、多哈希全量校验。
- DLL41 起：镜像优先数据源、分页回退、staged 安装/Java 回滚、版本重命名日志、
  单实例窗口唤醒、Mod 同名备份、更新源测速与 SHA512 校验、打包版可靠退出。
- DLL40 起：WMF 媒体后端、无 Tcl/Tk 闪屏、浅/深/中性三套图标、窗口四角填充式 1px 边框、
  启动不再整窗透明渐显；雪花/烟花最高 120 FPS，加载动画最高 60 FPS。
- 构建入口：`python build_slim.py`（当前正式发布）；`python build_onedir.py` 仅保留内部/回滚构建
- 安装包默认目录：`%LOCALAPPDATA%\Programs\SparkCraftLauncher`
- 不使用 UPX，不删除静态图片解码插件。

## DLL46 更新

- 下拉选择：版本类别改为独立自绘按钮 + `Qt.Tool` 弹层，按钮和弹层背景强制不透明；修复“点开又自动收起”；加入箭头旋转和淡入/滑入展开动画。
- 社区资源：来源、排序、分类、名称匹配全部切换为统一自绘下拉；第二行改为版本/分类/名称匹配弹性布局；删除所有社区资源页的“全部加载器”筛选。
- 加载器：Forge 在 BMCL/官方源不可用时回退 CurseForge 公开 modloader 列表；NeoForge 回退 GitCode 标签与本地安装器版本；OptiFine 缩短探测超时，无结果时隐藏而不是超时。
- 加载器 UI：超时阈值从 12 秒调整为 22 秒，避免备用源完成前被误判；加载失败/超时可点击重试，空版本隐藏。
- Java：补齐 Forge/NeoForge/Fabric/OptiFine/LiteLoader 与 26.x JSON 的 Java 上下限；启动和安装统一使用该区间。
- 整合包：扩展图标候选、`iconKey`/远程图标、`pmgl_pack.json` 名称与递归 Mod 数量校验；安装到现有实例前备份，失败不删除。
- 新闻：Archive 使用真实 `EventType/EventData`，支持 JSON/XAML、嵌套分组、缓存与重试；26.1 封面使用官方图并避免占位图缓存。
- 列表/UI：版本列表、资源中心、Mod 管理和主页统一使用动画列表；版本头标签、圆角和展开/收起动画统一；加载失败状态覆盖全页面重试。
- 启动：图形 fatal 先观察窗口稳定性；Java 上下文、版本链和任务栏进度合并；下载/安装阶段与真实 worker phase 对齐。

## DLL45 更新

- 背景视频：固定 Windows Media Foundation，普通/精简构建均排除 FFmpeg 插件和运行库；
  增加后端、插件、错误码和媒体状态日志；只把 Playing/Paused 当作可用状态，失败不再
  让播放按钮来回闪烁；黑帧采样、1.8 秒超时和 InvalidMedia 回退保留。
- 版本安装：官方 Piston/launchermeta、BMCLAPI、OpenBMCLAPI 多源并发查询，逐源记录
  HTTP 状态、耗时和失败原因；v1 不足时再用 v2 和按版本 ID 直取接口；26.3 元数据实测
  3.7 秒获取，114 个 libraries；安装日志 `install_<MC>_<实例>.log` 保证非空，失败清理
  本次新建目录。
- 版本源：设置中的“仅官方源”不再请求镜像；“优先 BMCLAPI”时镜像数据优先；官方和
  镜像使用独立清单缓存；版本列表先显示缓存，刷新失败保留缓存并显示重试。
- `.old`/更新：清理前结束仍从备份运行的进程，父进程结束前校验 exe 路径；失败回执
  在下次启动显示明确原因。
- Mod 统计：递归扫描 `mods` 及 `META-INF/jars`、`jarjar`、`libraries` 嵌套 Jar，
  按 modid 去重；Mod 管理器显示嵌套/系统模块，嵌套项只读；数量与启动器实际加载一致。
- 整合包：支持“安装到现有实例”，校验加载器和 MC 版本，先备份 `mods/config/icon/
  pmgl_pack.json`，失败不删除现有版本；同名实例不再自动生成 `(2)` 副本。
- 版本列表：顶部“最新版本”固定为最新正式版 + 最新预览版；从各自列表排除最新项；
  预览版 pre/rc 排在 snapshot 前；不再显示 `[最新]`、数量或“推荐最佳版本”。
- 新闻：移除硬编码 wiki 入口，使用 `EventType/EventData` 打开内置 ArchiveListPage；
  支持 `VersionArchiveList.json/.xaml`、内部嵌套分组、版本详情、返回链路、缓存和重试；
  只有 `打开网页` 条目允许调用外部浏览器。
- UI：版本详情加载遮罩居中；刷新按钮右对齐；列表选中项和下拉/菜单弹出项圆角；
  返回按钮统一圆形；浅色主题按钮和标题/设置文字可见；清空标题图片立即移除旧图。
- 图形：启动前检查显示适配器，只拦截 Microsoft Basic Display/无适配器；OpenGL/
  Vulkan 失败提示包含实际显卡和建议，不硬改不支持的图形后端。

## DLL44 更新

- 更新残留：`cleanup_old_executable()` 每一轮删除前先结束仍从 `.old` 运行的进程；
  `psutil` 不可用时按文件名 `taskkill /F /IM` 兜底。onedir PowerShell 更新器增加
  当前/父进程等待超时与超时强制结束，只有父进程路径等于当前启动器 exe 才等待/结束；
  删除备份前按 `.old` 路径清理占用进程，清理不完整不再静默返回 0。
- 背景视频：首帧和后续帧做亮度采样，全黑帧不覆盖 fallback；连续黑帧 1.8 秒判定失败，
  `InvalidMedia` 立即回退，停止视频时重置黑帧状态。
- 快照 Mod：保留 `26.4-snapshot-10` 完整版本号；文件按完整 `game_versions` 精确匹配，
  未精确命中时按文件支持的最高版本分组，不再把快照误报为无文件。
- 整合包：导入页支持“新建实例 / 安装到现有实例”；现有实例模式校验加载器和
  Minecraft 版本、跳过加载器安装、保留已有文件，失败不删除整个版本目录。
- 依赖：同时展示 required/optional；Fabric API、Quilt API 归入基础 API 组，
  可选依赖默认折叠。
- UI：删除“版本类型”文字；版本胶囊改为 QPainter 自绘并保留 180ms 切换动画；
  返回按钮继续保持 38×38 自绘圆形。

## DLL43 更新

- 启动优化：背景图首帧后延迟加载、页面按需构建、`showEvent` 单次守卫、导航去重、
  版本扫描单次 JSON 读取与 mtime/size 缓存；源码隔离启动窗口约 0.88–0.98s。
- Windows 原生任务栏进度：新增 `core/taskbar_progress.py`（`ITaskbarList3`），与
  `DownloadManager.snapshot()` 同步，覆盖下载、启动准备和整合包导入；关闭时释放 COM。
- 关闭流程：`shutdown_media()` 停止背景视频/音乐后，再落盘配置/日志并 `os._exit`；
  打包版连续关闭实测约 0.54s，无残留进程。
- 启动修复：目录/JSON 快检后只跑一次 `RepairEngine`；`missing_only` 同时检查已知大小和
  JAR/ZIP 结构；空 classpath 直接报错，不再生成 `-cp ""`；`-Xms` 严格使用用户最小内存。
- UI：正式版/快照版改为圆角胶囊；返回按钮统一圆形；版本列表头改小圆角标签；
  行高统一 38px、图标 20×20；长名称省略+tooltip；未选文件时下载按钮禁用；
  文件列表回顶；适配/其他版本/无文件状态色分级；下载页与详情页进度条统一 12px/6px。
- 运行中删除版本在 Worker 内二次检查；配置保存继续使用 RLock 与原子替换。

## DLL42 更新

- 内存文本统一转换为整数 MB：`3715M`→3715、`3.6G`→3686、`512M`→512；启动参数使用
  `-Xmx3715m` / `-Xms512m` 小写 m，配置保存一律写整数 MB，界面显示仍可为 `3.6G`。
- 导出启动脚本改为 PCL 风格：`chcp 936`、`@echo off`、`title`、启动/退出提示、
  `cd /D`、完整 `java.exe` 命令行和 `pause`；`%` 双写、特殊字符参数加引号，
  超长 classpath 使用 `set "CP=..."` 分段；文件保持 GBK + CRLF。
- Forge / NeoForge 在线安装方式 B 不再使用 Cleanroom，依次回退 BMCL、OpenBMC
  Maven 镜像和官方源。
- 更新回执拆分为 `pending_update.json`（installing）、`updated_update.json`（成功）和
  `failed_update.json`；只有复制并校验成功才写 updated，启动提示只消费 updated。
  onedir 更新不再在 PowerShell 启动后立即清下载标记，失败不会再误报“已成功更新”。
- `staged_download` 默认拒绝空暂存文件（`allow_empty=False`）；`HttpClient._verify`
  对同时提供的 SHA1/SHA256/SHA512 全部校验。
- 版本重命名回滚时，若原配置文件不存在则删除回滚过程中新建的配置，不留下默认配置。
- 设置页分组顶部边距保持 `(24, 16, 24, 18)`，本轮未改 UI 布局。

## DLL41 更新

- CurseForge 镜像请求不再发送 `x-api-key`；搜索、项目详情和分类接口增加镜像回退，
  修复镜像不可用时误显示“无结果”；补齐分页，移除未过滤文件兜底。
- Modrinth 按设置优先镜像或官方，搜索/版本分页使用 100 条偏移；接口错误正常向上抛出，
  不再静默切换不兼容版本。
- 下载统一使用 staged 临时文件与 `.part.meta`（URL/ETag/Last-Modified/哈希绑定），
  校验大小、SHA1/SHA256、zip 结构和目标锁后再原子替换；失败保留旧文件。
- 安装器下载支持 Maven `.sha1` 校验和 zip 校验；Java 运行时先解压到临时目录、校验
  `bin/javaw.exe`/`release`/主版本后再原子换目录，失败自动回滚，启动时清理旧运行时。
- 版本重命名改为 `RenamePlan`：预检所有 JSON/子版本/配置/档案，先备份并写 journal，
  两步提交；任一步失败逆序回滚，崩溃后启动自动恢复。
- 配置写入增加数值校验与钳制：内存上下限、下载线程 1–100、速度非负有限值、
  音量 0–100、窗口尺寸 320–10000；非法值拒绝而不是写入损坏配置。
- 单实例改为互斥锁 + `QLocalServer`/`QLocalSocket` IPC；重复启动会恢复并置顶已有窗口。
- Mod 拖入遇到同名文件先确认，旧文件备份为 `.bak-<时间戳>-<uuid>`，再原子替换。
- 更新检查收集多个发布响应并按 `(版本, DLL_BUILD)` 选择最高；Range 测速排序候选，
  校验 PE 头，更新标记记录附件大小/摘要/本地 SHA256/尝试次数，prepare 失败正确上报。
- 设置页分组顶部边距恢复为 `(24, 16, 24, 18)`，`RoundedGroupBox` 的 `padding-top` 保持 0。
- 修复打包版关闭卡死：`closeEvent` 同步落盘配置/日志后直接 `os._exit`，不再等待
  Qt/WMF 的 `stop/deleteLater/processEvents` 析构；线程回收不再调用可能死锁的
  `QThread.terminate()`。实测打包版点击关闭约 0.5 秒退出（连续 3 次），无隐藏残留进程。

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
| DLL43 onefile 冷启动（当前开发机实测） | 约 1.06–1.21s | - |
| DLL42 onefile 冷启动（历史实测） | 约 1.3s | - |
| DLL41 onefile 冷启动（历史实测） | 约 1.2s | - |
| DLL40 onefile 冷启动（历史实测） | 约 1.5s | - |
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

DLL39 onefile 为 36,662,822 字节；DLL44 为 29,926,348 字节，减少
6,736,474 字节（18.37%），落在 28–30 MB 目标区间；相比 DLL43 增加
11,742 字节，来自更新器清理、黑帧检测、整合包实例选择和依赖分组逻辑。主要变化：

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
