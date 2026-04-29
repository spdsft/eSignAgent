; ============================================================================
; eSignAgent — Windows installer (Inno Setup 6.x)
;
; Build:  ISCC.exe installer.iss
; Output: dist\eSignAgent-Setup-{version}.exe
;
; What it does on the target machine (admin elevation required):
;   1. Installs esignagent-host.exe under %ProgramFiles%\eSignAgent\
;   2. Writes HKLM Native Messaging registrations for Chrome, Edge, Brave,
;      Firefox so each browser can spawn the host.
;   3. Writes HKLM\Software\Policies\<vendor>\<browser>\ExtensionInstallForcelist
;      so Chrome and Edge auto-install the extension at next startup.
;      The user cannot disable it, and the extension auto-updates from the
;      stable update_url.
;   4. Registers an uninstaller.
; ============================================================================

#define AppName "eSignAgent"
#define AppVersion "1.0.1"
#define AppPublisher "eSignAgent"
#define AppExeName "esignagent-host.exe"
#define HostName "com.esignagent.host"
#define ExtensionId "aoonbefkefmhoicoceilifnngkenmfah"
#define ExtensionUpdateUrl "https://github.com/spdsft/eSignAgent/releases/download/v1.0.1/update.xml"

[Setup]
AppId={{8C5E4E9F-2E1A-4E2B-9F4A-eSignAgent01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=dist
OutputBaseFilename=eSignAgent-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableDirPage=yes
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}
WizardStyle=modern
SetupIconFile=
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} signing host installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\native-host\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

; ----------------------------------------------------------------------------
; Native Messaging manifest — written as a JSON file at install time, then
; pointed at via HKLM registry entries (which is how Chromium browsers and
; Firefox locate native messaging hosts on Windows).
; ----------------------------------------------------------------------------

[Code]
const
  HostNameConst = '{#HostName}';
  ExtensionIdConst = '{#ExtensionId}';

procedure WriteJsonManifest(const ManifestPath, AllowedField, AllowedValue: string);
var
  ExePath, Json: string;
  Lines: TArrayOfString;
begin
  ExePath := ExpandConstant('{app}\{#AppExeName}');
  StringChangeEx(ExePath, '\', '\\', True);

  Json :=
    '{' + #13#10 +
    '  "name": "' + HostNameConst + '",' + #13#10 +
    '  "description": "eSignAgent native host",' + #13#10 +
    '  "path": "' + ExePath + '",' + #13#10 +
    '  "type": "stdio",' + #13#10 +
    '  "' + AllowedField + '": ["' + AllowedValue + '"]' + #13#10 +
    '}';

  ForceDirectories(ExtractFilePath(ManifestPath));
  SetArrayLength(Lines, 1);
  Lines[0] := Json;
  SaveStringsToFile(ManifestPath, Lines, False);
end;

procedure InstallManifests;
var
  AppDir: string;
  ChromiumOrigin, FirefoxId: string;
begin
  AppDir := ExpandConstant('{app}');
  ChromiumOrigin := 'chrome-extension://' + ExtensionIdConst + '/';
  FirefoxId := ExtensionIdConst;

  // One JSON for Chromium browsers, one for Firefox (different field name)
  WriteJsonManifest(AppDir + '\' + HostNameConst + '.chromium.json', 'allowed_origins', ChromiumOrigin);
  WriteJsonManifest(AppDir + '\' + HostNameConst + '.firefox.json',  'allowed_extensions', FirefoxId);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    InstallManifests;
end;

[Registry]
; --- Native Messaging host registrations (HKLM: works for any user on this machine) ---
Root: HKLM; Subkey: "Software\Google\Chrome\NativeMessagingHosts\{#HostName}"; \
  ValueType: string; ValueName: ""; \
  ValueData: "{app}\{#HostName}.chromium.json"; \
  Flags: uninsdeletekey

Root: HKLM; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\{#HostName}"; \
  ValueType: string; ValueName: ""; \
  ValueData: "{app}\{#HostName}.chromium.json"; \
  Flags: uninsdeletekey

Root: HKLM; Subkey: "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\{#HostName}"; \
  ValueType: string; ValueName: ""; \
  ValueData: "{app}\{#HostName}.chromium.json"; \
  Flags: uninsdeletekey

Root: HKLM; Subkey: "Software\Mozilla\NativeMessagingHosts\{#HostName}"; \
  ValueType: string; ValueName: ""; \
  ValueData: "{app}\{#HostName}.firefox.json"; \
  Flags: uninsdeletekey

; --- Force-install the browser extension via enterprise policy ---
; Chrome / Edge fetch this list at startup and auto-install the extensions
; from the update_url. The extension cannot be disabled by the user (intentional).
Root: HKLM; Subkey: "Software\Policies\Google\Chrome\ExtensionInstallForcelist"; \
  ValueType: string; ValueName: "1"; \
  ValueData: "{#ExtensionId};{#ExtensionUpdateUrl}"; \
  Flags: uninsdeletevalue

Root: HKLM; Subkey: "Software\Policies\Microsoft\Edge\ExtensionInstallForcelist"; \
  ValueType: string; ValueName: "1"; \
  ValueData: "{#ExtensionId};{#ExtensionUpdateUrl}"; \
  Flags: uninsdeletevalue

[UninstallDelete]
Type: files; Name: "{app}\{#HostName}.chromium.json"
Type: files; Name: "{app}\{#HostName}.firefox.json"
Type: filesandordirs; Name: "{localappdata}\eSignAgent\logs"
