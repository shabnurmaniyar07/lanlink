; Inno Setup script for LanLink.
;
; Build the application first (packaging\build.bat does both):
;     pyinstaller packaging\lanlink.spec --noconfirm
;     iscc packaging\lanlink.iss
;
; Produces packaging\output\LanLinkSetup-<version>.exe

#define AppName "LanLink"
#define AppPublisher "LanLink"
#define AppExe "LanLink.exe"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{8F2A6E14-4C7B-4E63-9E52-2C6D1B9A7F30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=LanLinkSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-machine so the firewall rule below can be added; the installer asks for
; elevation once and never again.
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExe}
; Upgrading in place is the normal case: same AppId, so Windows replaces the
; existing installation instead of leaving two copies behind.
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start {#AppName} when I sign in"; GroupDescription: "Startup:"; Flags: unchecked
Name: "firewall"; Description: "Allow {#AppName} through Windows Firewall on &private networks"; GroupDescription: "Network:"

[Files]
Source: "..\dist\LanLink\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
; Private networks only. LanLink binds the LAN address by default and has no
; business being reachable from a public network such as an airport hotspot.
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""{#AppName}"" dir=in action=allow program=""{app}\{#AppExe}"" enable=yes profile=private"; \
  Flags: runhidden; StatusMsg: "Adding the firewall rule for private networks..."; Tasks: firewall

Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""{#AppName}"""; \
  Flags: runhidden; RunOnceId: "RemoveFirewallRule"

[UninstallDelete]
; The staging cache is a cache: regenerated on demand, and nobody wants it left
; behind. Settings, the device identity and its certificate are NOT touched —
; removing them would silently break every pairing on a reinstall.
Type: filesandordirs; Name: "{localappdata}\LanLink\staging"
Type: filesandordirs; Name: "{localappdata}\LanLink\thumbnails"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    MsgBox('LanLink has been removed.' + #13#10 + #13#10 +
           'Your settings, this device''s identity and its paired devices are still in' + #13#10 +
           ExpandConstant('{userappdata}') + '\..\..\.lanlink-hub' + #13#10 + #13#10 +
           'Delete that folder as well if you do not intend to reinstall.',
           mbInformation, MB_OK);
  end;
end;
