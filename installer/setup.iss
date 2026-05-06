; ContentForge — Inno Setup Script
; Compile with: iscc setup.iss
; Download Inno Setup: https://jrsoftware.org/isinfo.php

#define AppName "ContentForge"
#define AppVersion "1.0"
#define AppPublisher "ContentForge"
#define AppURL "https://github.com"
#define AppExeName "ContentForge.exe"

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
OutputBaseFilename=ContentForge_Setup_v{#AppVersion}
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
VersionInfoDescription=ContentForge Video Editor

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";    Description: "Criar ícone na Área de Trabalho"; GroupDescription: "Ícones adicionais:"; Flags: unchecked
Name: "quicklaunchicon"; Description: "Criar ícone na Barra de Tarefas Rápida"; GroupDescription: "Ícones adicionais:"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
; Main executable (built by PyInstaller)
Source: "..\dist\ContentForge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

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
  IsUpdate := RegQueryStringValue(HKCU, 'Software\ContentForge', 'Version', ExistingVer);
  if IsUpdate then begin
    MsgBox('Uma versão anterior do ContentForge foi detectada.' + #13#10 +
           'O instalador irá atualizar para a versão {#AppVersion}.', mbInformation, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    RegWriteStringValue(HKCU, 'Software\ContentForge', 'Version', '{#AppVersion}');
    RegWriteStringValue(HKCU, 'Software\ContentForge', 'InstallPath', ExpandConstant('{app}'));
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := MsgBox('Tem certeza que deseja desinstalar o ContentForge?', mbConfirmation, MB_YESNO) = IDYES;
end;
