; AppData 磁盘清理助手 —— Inno Setup 6 安装包脚本（2.2.0 新增）
; 由 build_installer.bat 调用：
;   ISCC.exe /DAppVersion=2.2.0 installer\AppDataCleaner.iss
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
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist
OutputBaseFilename=AppDataCleaner-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName} v{#AppVersion}
UninstallDisplayIcon={app}\{#AppExe}
; 未做代码签名：安装包与 exe 首次运行可能触发 SmartScreen 提示

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
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
