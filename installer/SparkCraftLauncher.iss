; Spark Craft Launcher - per-user onedir installer
; Build: "D:\Inno Setup 6\ISCC.exe" installer\SparkCraftLauncher.iss

#define MyAppName "Spark Craft Launcher"
#define MyAppVersion "1.11.4"
#define MyAppExeName "Spark Craft Launcher.exe"
#define MyAppPublisher "Spark Craft"
#define MyAppURL "https://github.com/Jimhow-Gu/Spark-Craft-Launcher"

[Setup]
AppId={{252E8F68-DC09-4634-A045-9259F1D24758}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} V{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\SparkCraftLauncher
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist_installer
OutputBaseFilename=V{#MyAppVersion}-Setup-SparkCraftLauncher
SetupIconFile=..\ui\imgs\sparkler.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=Local\SparkCraftLauncher.SingleInstance.v1
SetupLogging=yes
VersionInfoVersion=1.11.4.35
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoDescription=Spark Craft Launcher Installer

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked
Name: "startmenuicon"; Description: "创建开始菜单快捷方式"; GroupDescription: "附加任务："

[Dirs]
; SCL 是用户数据（配置、皮肤、缓存、日志），安装/升级/卸载都不删除。
Name: "{app}\SCL"; Flags: uninsneveruninstall

[InstallDelete]
; onedir 必须成套替换，避免旧版 _internal 中残留 DLL/插件与新版本混用。
Type: files; Name: "{app}\{#MyAppExeName}"
Type: files; Name: "{app}\{#MyAppExeName}.old"
Type: files; Name: "{app}\{#MyAppExeName}.*.old"
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\_internal.old"

[Files]
Source: "..\dist_onedir\SparkCraftLauncher\*"; DestDir: "{app}"; Excludes: "SCL\*,SCL"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startmenuicon
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startmenuicon

[Run]
; 安装后先做一次无窗口预热，减少 Windows 首次执行的实时扫描对启动时间的影响。
Filename: "{app}\{#MyAppExeName}"; Parameters: "--prewarm"; Flags: runhidden waituntilterminated; StatusMsg: "正在预热启动组件..."
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 自动更新后的 _internal 文件不在 Inno 的安装清单里，卸载时整体清理；
; SCL 用户数据不在此列表中，始终保留。
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#MyAppExeName}.old"
Type: files; Name: "{app}\{#MyAppExeName}.*.old"
Type: filesandordirs; Name: "{app}\_internal.old"
