; one-click windows installer: no wizard pages, no admin prompt.
; double-click -> installs to the user's app folder in seconds -> opens aureon.
#define AppVer GetEnv("AUREON_VERSION")

[Setup]
AppId={{6E0A3C1D-7B52-4C55-9E3B-A0E5C0D1A7F1}
AppName=aureon
AppVersion={#AppVer}
AppVerName=aureon {#AppVer}
AppPublisher=aureon
DefaultDirName={localappdata}\Programs\aureon
PrivilegesRequired=lowest
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
ShowLanguageDialog=no
CloseApplications=yes
WizardStyle=modern
SetupIconFile=aureon.ico
UninstallDisplayIcon={app}\aureon.exe
UninstallDisplayName=aureon
OutputDir=..\dist
OutputBaseFilename=aureon-setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\aureon\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\aureon"; Filename: "{app}\aureon.exe"
Name: "{autodesktop}\aureon"; Filename: "{app}\aureon.exe"

[Run]
; opens the app right after installing (skipped for silent installs, e.g. ci)
Filename: "{app}\aureon.exe"; Flags: nowait skipifsilent
