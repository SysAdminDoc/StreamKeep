; StreamKeep Windows installer (V35) - UNSIGNED BY POLICY.
;
; No SignTool step, no certificate, no notarization. SmartScreen will warn on
; first run of a downloaded build; the answer is "More info" then "Run anyway",
; or verify the published SHA-256 hash before running it.
;
; Built by packaging/build.py --installer, which stamps AppVersion and SourceDir
; from the onedir tree it just produced.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\StreamKeep"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{4E4C1B2F-6B7A-4C57-9E1B-1C0F0A9D5E31}
AppName=StreamKeep
AppVersion={#AppVersion}
AppPublisher=SysAdminDoc
AppPublisherURL=https://github.com/SysAdminDoc/StreamKeep
DefaultDirName={autopf}\StreamKeep
DefaultGroupName=StreamKeep
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\StreamKeep.exe
OutputDir={#OutputDir}
OutputBaseFilename=StreamKeep-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; A per-user install needs no elevation; the installer asks only when the
; operator chooses a machine-wide location.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
; The whole onedir tree. Every launch reads these files in place, so there is
; no per-launch temp extraction to be slow or to race a second launch.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\StreamKeep"; Filename: "{app}\StreamKeep.exe"
Name: "{group}\Uninstall StreamKeep"; Filename: "{uninstallexe}"
Name: "{autodesktop}\StreamKeep"; Filename: "{app}\StreamKeep.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StreamKeep.exe"; Description: "Launch StreamKeep"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller writes nothing outside {app}; the user profile directory is left
; alone so an uninstall never destroys a library, history, or queue.
Type: filesandordirs; Name: "{app}\_internal"
