; Inno Setup script for Folder Sorter
; Build with build_installer.bat (needs Inno Setup 6.4 or newer: https://jrsoftware.org/isinfo.php)

#define MyAppName "Folder Sorter"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Jushua Pabelic"
#define MyAppURL "https://github.com/Pabelic1221/File-sorter"
#define MyAppExe "FolderSorter.exe"

[Setup]
AppId={{B7C1E5A2-4D3F-4E8A-9C61-5F2A7D90E1B3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=FolderSorter-Setup-{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
WizardStyle=modern
WizardImageFile=installer\wizard-large.bmp
WizardSmallImageFile=installer\wizard-small.bmp
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; remove the app's saved settings (theme, last folder) when uninstalling
Type: filesandordirs; Name: "{%USERPROFILE}\.foldersorter"

[Code]
function WebView2Installed(): Boolean;
begin
  Result :=
    RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') or
    RegKeyExists(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') or
    RegKeyExists(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
    MsgBox('The Microsoft Edge WebView2 Runtime was not detected. Folder Sorter will still run, but it needs this runtime for its custom window. You can get it free from: https://go.microsoft.com/fwlink/p/?LinkId=2124703', mbInformation, MB_OK);
end;
