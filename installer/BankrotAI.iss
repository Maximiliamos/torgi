#define MyAppName "BankrotAI"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "BankrotAI"
#define MyAppExeName "BankrotAI.exe"

[Setup]
AppId={{507AD9DA-CC79-4E2E-A893-785928881158}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\BankrotAI
DefaultGroupName=BankrotAI
OutputDir=..\build_current\installer
OutputBaseFilename=BankrotAI-Setup-{#MyAppVersion}
SetupIconFile=..\src\bankrotai\assets\app.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\build_current\app\BankrotAI.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\BankrotAI"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\BankrotAI"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить BankrotAI"; Flags: nowait postinstall skipifsilent
