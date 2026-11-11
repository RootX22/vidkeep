; The Windows installer. packaging\windows\build.ps1 passes AppVersion, SourceDir, IconFile and OutputDir.
; Installs for the current user by default, so it needs no administrator rights.

[Setup]
AppId={{6F1E2B7C-5A8D-4C3E-9B2F-1D7A0C4E8B35}
AppName=vidkeep
AppVersion={#AppVersion}
AppVerName=vidkeep {#AppVersion}
AppPublisher=Mohamed Mohsen (root0x0)
AppPublisherURL=https://github.com/RootX22/vidkeep
AppSupportURL=https://github.com/RootX22/vidkeep/issues
AppCopyright=Made with love by Mohamed Mohsen (root0x0) in Egypt
DefaultDirName={autopf}\vidkeep
DefaultGroupName=vidkeep
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=vidkeep-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Vidkeep.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile={#SourceDir}\LICENSE

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\vidkeep"; Filename: "{app}\Vidkeep.exe"
Name: "{autodesktop}\vidkeep"; Filename: "{app}\Vidkeep.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Vidkeep.exe"; Description: "{cm:LaunchProgram,vidkeep}"; Flags: nowait postinstall skipifsilent
