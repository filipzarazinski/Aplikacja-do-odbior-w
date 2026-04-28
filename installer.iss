#define AppName "Odbiory"
#define AppVersion "1.1.2"
#define AppPublisher "filipzarazinski"
#define AppURL "https://github.com/filipzarazinski/Aplikacja-do-odbior-w"
#define AppExeName "Odbiory.exe"

[Setup]
AppId={{8F3A2C1D-4B5E-4F6A-9D2E-1C3B7A8F0E2D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=Odbiory_Setup
SetupIconFile=favicon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=yes
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "desktopicon"; Description: "Utwórz skrót na pulpicie"; GroupDescription: "Skróty:"; Flags: unchecked

[Files]
Source: "dist\Odbiory\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Odinstaluj {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Uruchom {#AppName}"; Flags: nowait postinstall skipifsilent
