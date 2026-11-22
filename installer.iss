; Скрипт установщика для Inno Setup 6 (обычная установка, без Store).
;
; Сборка:  iscc installer.iss
; Требуется установленный Inno Setup: winget install JRSoftware.InnoSetup
; Файл проверен статически; на этом компьютере Inno Setup не установлен,
; поэтому сборка установщика здесь не запускалась.
;
; Что делает установщик:
;  * копирует приложение в %LOCALAPPDATA%\Programs\Нарезка Shorts (без прав администратора);
;  * создаёт ярлыки в меню «Пуск» и (по желанию) на рабочем столе;
;  * регистрирует деинсталлятор в «Установка и удаление программ»;
;  * кладёт рядом лицензии и политику конфиденциальности.
;
; ВАЖНО: без цифровой подписи установщик и приложение блокируются Smart App Control
; на компьютерах, где эта защита включена. Для публикации нужен сертификат подписи
; кода (см. README, раздел про .exe) либо распространение через Microsoft Store.

#define AppName "Нарезка Shorts"
#define AppVersion "1.0.0"
#define AppPublisher "Нарезка Shorts"
#define AppExeName "Нарезка Shorts.exe"
#define SourceDir "dist\Нарезка Shorts"

[Setup]
AppId={{8E4B6F52-6C4A-4F6E-9A3B-1D2C7E5F0A31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist\installer
OutputBaseFilename=Нарезка-Shorts-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
LicenseFile=store\ЛИЦЕНЗИИ.md
InfoBeforeFile=README.md
MinVersion=10.0.17763

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; \
    GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
; Всё приложение целиком: сам .exe, ffmpeg и библиотеки рядом.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "store\ПОЛИТИКА-КОНФИДЕНЦИАЛЬНОСТИ.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "store\ЛИЦЕНЗИИ.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "store\ОПИСАНИЕ.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Политика конфиденциальности"; Filename: "{app}\docs\ПОЛИТИКА-КОНФИДЕНЦИАЛЬНОСТИ.md"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Запустить {#AppName}"; \
    Flags: nowait postinstall skipifsilent
