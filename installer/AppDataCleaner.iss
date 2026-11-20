; AppData 磁盘清理助手 —— Inno Setup 6 安装包脚本（2.2.0 新增）
; 由 build_installer.bat 调用：
;   ISCC.exe /DAppVersion=2.3.0 installer\AppDataCleaner.iss
; 版本号由构建脚本从 server.py: APP_VERSION 读取，本文件不写第二份真值。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "AppData 磁盘清理助手"
#define AppExe "AppDataCleaner.exe"

[Setup]
AppId={{B3F0F3B2-6E4C-4B7B-9E0C-6E5B4D8F2A17}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=AppData Cleaner
DefaultDirName={localappdata}\Programs\AppDataCleaner
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 当前用户权限安装：不写 Program Files、不注册服务、不改系统目录
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=AppDataCleaner-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName} v{#AppVersion}
UninstallDisplayIcon={app}\{#AppExe}
; 未做代码签名：安装包与 exe 首次运行可能触发 SmartScreen 提示

[Files]
; 2.3 起安装版为 onedir 目录形态（免解压启动）；打包 onedir 产物整体，排除本地运行留下的配置/日志/缓存
Source: "..\dist\AppDataCleaner\*"; DestDir: "{app}"; Excludes: "config.json,config.json.bak,config.json.bad,logs\*,cache\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
{ 卸载默认保留用户配置与历史日志；只有明确选择「是」才删除。 }
function InitializeUninstall(): Boolean;
var
  Answer: Integer;
begin
  Result := True;
  { 静默卸载不弹窗、也不删配置：无人值守场景下「保留」才是安全默认 }
  if UninstallSilent then
    Exit;
  Answer := MsgBox('是否同时删除配置与历史日志？' + #13#10 +
    '选择「否」将保留 config.json 与 logs 目录，重新安装后设置与历史仍在。',
    mbConfirmation, MB_YESNO);
  if Answer = IDYES then
  begin
    DeleteFile(ExpandConstant('{app}\config.json'));
    DeleteFile(ExpandConstant('{app}\config.json.bak'));
    DeleteFile(ExpandConstant('{app}\config.json.bad'));
    DelTree(ExpandConstant('{app}\logs'), True, True, True);
    DelTree(ExpandConstant('{app}\cache'), True, True, True);
  end;
end;
