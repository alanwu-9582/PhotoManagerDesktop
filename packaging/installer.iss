; Photo Manager 安裝檔（Inno Setup 6）
; 通常用 build.bat 產生；手動：ISCC /DAppVersion=1.0.0 installer.iss（要先跑過 PyInstaller）
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Photo Manager"
#define AppExe "PhotoManager.exe"

[Setup]
AppId={{6E3B7B8C-3F0A-4B9E-9C55-2B7C1E6A9F41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=PhotoManager
AppPublisherURL=https://wayne-1211.github.io/photoManager/
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 預設只裝給目前使用者（不用系統管理員權限），也可以選裝給所有人
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=PhotoManager-Setup-{#AppVersion}
SetupIconFile=PhotoManager.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ShowLanguageDialog=auto

[Languages]
Name: "zh_TW"; MessagesFile: "compiler:Default.isl,ChineseTraditional.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
zh_TW.DeleteUserData=要一併刪除 Photo Manager 的設定、分類與標記紀錄嗎？%n%n（照片本身不會被刪除。選「否」的話，之後重新安裝還能接著用。）
en.DeleteUserData=Also delete Photo Manager's settings, categories and marks?%n%n(Your photos are never touched. Choose No to keep them for a future reinstall.)

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\PhotoManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 升級時先清掉舊版的函式庫，免得新舊混在一起
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
  begin
    { 縮圖快取一定刪（可以重建）；設定與標記先問 }
    DelTree(ExpandConstant('{localappdata}\PhotoManager'), True, True, True);
    if MsgBox(CustomMessage('DeleteUserData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{userappdata}\PhotoManager'), True, True, True);
  end;
end;
