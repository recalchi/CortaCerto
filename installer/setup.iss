; CortaCerto - Inno Setup Script
; Compile with: iscc setup.iss
; Download Inno Setup: https://jrsoftware.org/isinfo.php

#define AppName "CortaCerto"
#define AppVersion "1.0"
#define AppPublisher "CortaCerto"
#define AppURL "https://github.com"
#define AppExeName "CortaCerto.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE.txt
OutputDir=..\dist\installer
OutputBaseFilename=CortaCerto_Setup_v{#AppVersion}
SetupIconFile=..\corta_certo_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppVersion}.0.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=CortaCerto Video Editor

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";     Description: "Criar icone na Area de Trabalho"; GroupDescription: "Icones adicionais:"; Flags: unchecked
Name: "quicklaunchicon"; Description: "Criar icone na Barra de Tarefas Rapida"; GroupDescription: "Icones adicionais:"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
; Main executable (built by PyInstaller)
Source: "..\dist\CortaCerto\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExeName}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Iniciar {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  UpdatePage: TWizardPage;
  IsUpdate: Boolean;

procedure InitializeWizard;
var
  ExistingVer: String;
begin
  IsUpdate := RegQueryStringValue(HKCU, 'Software\CortaCerto', 'Version', ExistingVer);
  if IsUpdate then begin
    MsgBox('Uma versao anterior do CortaCerto foi detectada.' + #13#10 +
           'O instalador ira atualizar para a versao {#AppVersion}.', mbInformation, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    RegWriteStringValue(HKCU, 'Software\CortaCerto', 'Version', '{#AppVersion}');
    RegWriteStringValue(HKCU, 'Software\CortaCerto', 'InstallPath', ExpandConstant('{app}'));
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := MsgBox('Tem certeza que deseja desinstalar o CortaCerto?', mbConfirmation, MB_YESNO) = IDYES;
end;
