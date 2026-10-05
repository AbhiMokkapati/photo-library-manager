; installer.iss — Inno Setup script that packages the PyInstaller build
; (dist\PhotoLibraryManager\) into a proper Windows installer: Start Menu
; entry (so the app shows up in Windows search), Desktop shortcut, and a
; standard uninstaller entry in "Add or Remove Programs".
;
; Build with (after running build.ps1 at least once):
;     ISCC installer.iss
; or just run build_installer.ps1, which does both steps.
;
; Requires Inno Setup (ISCC.exe) on the build machine — a one-time build
; tool, not something end users need:
;     winget install JRSoftware.InnoSetup

#define MyAppName "Photo Library Manager"
; MyAppVersion is normally passed in from build_installer.ps1 via /DMyAppVersion=
; (read from core/version.py, the single source of truth) so this fallback only
; matters if you run ISCC.exe directly without going through that script.
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "Photo Library Manager"
#define MyAppExeName "PhotoLibraryManager.exe"

[Setup]
AppId={{B7C1F1F0-6E2A-4A9A-9B7E-8C6D8B6F2B10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=PhotoLibraryManagerSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; onedir PyInstaller build has many files/DLLs; no architecture restriction needed
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\PhotoLibraryManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu entry — this is what makes the app show up in Windows search
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
