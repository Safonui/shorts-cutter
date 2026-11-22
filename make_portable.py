# -*- coding: utf-8 -*-
"""Сборка переносимого комплекта «Нарезка Shorts» для отправки на другой компьютер.

Запуск:  .venv\\Scripts\\python.exe make_portable.py [--with-subtitles]

Что получается в папке `dist`:
* `Нарезка Shorts/` — само приложение (Python внутри, устанавливать не нужно);
* `УСТАНОВИТЬ.cmd` — установка для текущего пользователя: копирует приложение
  в %LOCALAPPDATA%\\Programs, делает ярлыки в «Пуск» и на рабочем столе,
  прописывает удаление в «Программы и компоненты». Права администратора не нужны;
* `УДАЛИТЬ.cmd` — удаление;
* `ПРОЧТИ-МЕНЯ.txt` — короткая инструкция для получателя;
* `Нарезка-Shorts-<версия>-portable.zip` — один архив, который и отправляется.

Почему так, а не установщик Inno Setup: он требует установки инструмента и
подписи кода. Здесь же всё собирается из стандартных средств Windows, ничего
доустанавливать не нужно и никаких затрат нет.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from appconfig import APP_NAME, APP_VERSION

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
PACKAGE = DIST / "portable"
APP_FOLDER_NAME = APP_NAME
EXE_NAME = f"{APP_NAME}.exe"

REPLACEMENTS = {"—": "-", "–": "-", "…": "...", "’": "'", "“": '"', "”": '"', "→": "->"}


def to_cp866(text: str) -> str:
    """Заменить символы, которых нет в cp866 — иначе .cmd не запишется."""
    result: list[str] = []
    for char in text:
        try:
            char.encode("cp866")
            result.append(char)
        except UnicodeEncodeError:
            result.append(REPLACEMENTS.get(char, "?"))
    return "".join(result)


SHORTCUT_PS1 = """# Ярлыки «Нарезка Shorts»: меню «Пуск» и рабочий стол.
# Вызывается установщиком: powershell -File ЯРЛЫКИ.ps1 -Exe "<путь к exe>"
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string]$MenuDir = "",
    [string]$DesktopDir = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Output "нет файла приложения: $Exe"
    exit 2
}

if (-not $MenuDir) {
    $MenuDir = Join-Path $env:APPDATA "Microsoft\\Windows\\Start Menu\\Programs"
}
if (-not $DesktopDir) {
    # Переменная SHORTS_DESKTOP_DIR нужна для автотестов; на обычной машине
    # используется настоящий путь рабочего стола (учитывает перенос в OneDrive).
    if ($env:SHORTS_DESKTOP_DIR) {
        $DesktopDir = $env:SHORTS_DESKTOP_DIR
    } else {
        $DesktopDir = [Environment]::GetFolderPath("Desktop")
    }
}

$shell = New-Object -ComObject WScript.Shell
$made = 0
foreach ($dir in @($MenuDir, $DesktopDir)) {
    if (-not (Test-Path -LiteralPath $dir)) {
        Write-Output "папка для ярлыка не найдена: $dir"
        continue
    }
    $linkPath = Join-Path $dir ((Split-Path $Exe -Leaf) -replace '\\.exe$', '') + ".lnk"
    try {
        $link = $shell.CreateShortcut($linkPath)
        $link.TargetPath = $Exe
        $link.WorkingDirectory = Split-Path $Exe
        $link.IconLocation = $Exe
        $link.Description = "Нарезка Shorts - вертикальные клипы 9:16"
        $link.Save()
        $made++
        Write-Output "ярлык создан: $linkPath"
    } catch {
        Write-Output ("не удалось создать ярлык в " + $dir + ": " + $_.Exception.Message)
    }
}
exit 0
"""


def install_script() -> str:
    """Скрипт установки для получателя (обычный текст, политика его не блокирует)."""
    return f"""@echo off
chcp 866 >nul
setlocal
title Установка: {APP_NAME}
cd /d "%~dp0"

set "SILENT="
if /i "%~1"=="/silent" set "SILENT=1"

set "SRC=%~dp0{APP_FOLDER_NAME}"
set "TARGET=%LOCALAPPDATA%\\Programs\\{APP_FOLDER_NAME}"

if not exist "%SRC%\\{EXE_NAME}" (
    echo Не найден файл приложения: "%SRC%\\{EXE_NAME}"
    echo Распакуйте архив целиком и запустите этот файл из распакованной папки.
    if not defined SILENT pause
    exit /b 1
)

echo Устанавливаю {APP_NAME} {APP_VERSION}...
if exist "%TARGET%" rd /s /q "%TARGET%"
mkdir "%TARGET%" >nul 2>&1
robocopy "%SRC%" "%TARGET%" /e /nfl /ndl /njh /njs /np >nul
if not exist "%TARGET%\\{EXE_NAME}" (
    echo Не удалось скопировать файлы в "%TARGET%".
    if not defined SILENT pause
    exit /b 1
)

echo Создаю ярлыки...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ЯРЛЫКИ.ps1" -Exe "%TARGET%\\{EXE_NAME}"

echo Прописываю удаление в «Программы и компоненты»...
set "UNINST=HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\ShortsCutter"
reg add "%UNINST%" /f /v DisplayName /t REG_SZ /d "{APP_NAME}" >nul
reg add "%UNINST%" /f /v DisplayVersion /t REG_SZ /d "{APP_VERSION}" >nul
reg add "%UNINST%" /f /v Publisher /t REG_SZ /d "{APP_NAME}" >nul
reg add "%UNINST%" /f /v InstallLocation /t REG_SZ /d "%TARGET%" >nul
reg add "%UNINST%" /f /v DisplayIcon /t REG_SZ /d "%TARGET%\\{EXE_NAME}" >nul
reg add "%UNINST%" /f /v NoModify /t REG_DWORD /d 1 >nul
reg add "%UNINST%" /f /v NoRepair /t REG_DWORD /d 1 >nul
reg add "%UNINST%" /f /v UninstallString /t REG_SZ /d "\\"%TARGET%\\УДАЛИТЬ.cmd\\"" >nul

echo.
echo Готово. Приложение установлено в:
echo   %TARGET%
echo Ярлыки: меню «Пуск» и рабочий стол.
echo.
if defined SILENT exit /b 0
choice /c ДВ /n /m "Запустить сейчас? [Д - да, В - выход] "
if errorlevel 2 goto :done
start "" "%TARGET%\\{EXE_NAME}"

:done
echo.
echo Удалить приложение можно через «Программы и компоненты»
echo или файлом УДАЛИТЬ.cmd в папке установки.
pause
"""


def uninstall_script() -> str:
    """Удаление: скрипт копирует себя во временную папку и удаляет файлы оттуда.

    Так можно убрать и сам скрипт: запущенный .cmd удалить нельзя, а его копия
    во временной папке — может.
    """
    return f"""@echo off
chcp 866 >nul
setlocal
title Удаление: {APP_NAME}
set "TARGET=%LOCALAPPDATA%\\Programs\\{APP_FOLDER_NAME}"

if /i "%~1"=="--delayed" goto delayed

echo Удаляю {APP_NAME}...
del /q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{APP_NAME}.lnk" >nul 2>&1
del /q "%USERPROFILE%\\Desktop\\{APP_NAME}.lnk" >nul 2>&1
del /q "%USERPROFILE%\\OneDrive\\Desktop\\{APP_NAME}.lnk" >nul 2>&1
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\ShortsCutter" /f >nul 2>&1
copy /y "%~f0" "%TEMP%\\shorts-uninstall.cmd" >nul 2>&1
start "" /min "%TEMP%\\shorts-uninstall.cmd" --delayed
echo Готово: ярлыки и запись в «Программах и компонентах» удалены,
echo файлы приложения удаляются...
exit /b 0

:delayed
timeout /t 3 >nul 2>&1
rd /s /q "%TARGET%" >nul 2>&1
del /q "%~f0" >nul 2>&1
exit /b 0
"""


def readme_text() -> str:
    """Инструкция для получателя архива."""
    return f"""{APP_NAME} {APP_VERSION} — переносимая сборка
=====================================================

ВАЖНО: СНАЧАЛА РАЗБЛОКИРУЙТЕ АРХИВ
----------------------------------
Windows помечает файлы, полученные из интернета или из мессенджера, и может
отказаться их запускать: «этот формат файла может быть небезопасен» или
«Windows защитила ваш компьютер».

Как это снять (один раз, 3 клика):
  1. Щёлкните по скачанному ZIP-архиву правой кнопкой → «Свойства».
  2. Внизу окна поставьте галочку «Разблокировать» (Unblock) → «ОК».
  3. Теперь распакуйте архив — файлы внутри уже будут без метки.

Если архив уже распакован, разблокируйте файлы в папке. Самый простой способ:
правый клик по «УСТАНОВИТЬ.cmd» → «Свойства» → «Разблокировать».
Можно и разом, в PowerShell:
  Get-ChildItem -Recurse | Unblock-File

Перенос на флешке метку не ставит — с флешки всё запускается сразу.

Что это
--------
Готовое приложение для Windows 10/11 (64-бит): режет длинное видео на
вертикальные клипы 9:16, вшивает субтитры, умеет находить яркие моменты по звуку.
Python и ffmpeg устанавливать не нужно — всё внутри.

Как установить (2 минуты)
-------------------------
1. Распакуйте архив целиком в любую папку (не запускайте файлы прямо из архива).
2. Запустите УСТАНОВИТЬ.cmd — приложение установится для текущего пользователя,
   права администратора не нужны.
3. Ярлык появится в меню «Пуск» и на рабочем столе.

Без установки
-------------
Приложение можно запускать прямо из папки «{APP_FOLDER_NAME}» двойным кликом по
{EXE_NAME} — без установки и без ярлыков.

Как удалить
-----------
В папке установки есть УДАЛИТЬ.cmd, либо «Программы и компоненты» →
«{APP_NAME}» → Удалить. Настройки остаются в %APPDATA%\\ShortsCutter — их можно
удалить вручную.

Субтитры
--------
Распознавание речи — отдельный модуль: при первом включении субтитров
приложение скачает модель (~460 МБ) из интернета и дальше работает офлайн.
Без интернета всё остальное работает полностью.

Если Windows пишет, что программа заблокирована политикой
---------------------------------------------------------
Сообщение «политика управления приложениями заблокировала этот файл» — это
Smart App Control: он не пропускает программы без цифровой подписи, и обойти его
кнопкой «всё равно запустить» нельзя. Варианты:
  * Параметры → Конфиденциальность и защита → Безопасность Windows →
    Управление приложениями и браузером → Smart App Control → выключить
    (учтите: обратно он включается только переустановкой Windows);
  * либо запускать на компьютере, где эта защита не включена.
На большинстве домашних компьютеров Smart App Control не мешает запуску.

Что внутри
----------
* ffmpeg — обработка видео, лицензия GPL: https://ffmpeg.org
* Python и Tcl/Tk — среда выполнения, лицензии PSF и BSD-подобная
* сторонние компоненты перечислены в файле ЛИЦЕНЗИИ.md
"""


def ensure_app_folder(with_subtitles: bool) -> Path:
    """Папка с собранным приложением (Python внутри)."""
    folder = DIST / APP_NAME
    if (folder / EXE_NAME).exists():
        return folder
    print("Собираю приложение в папку (это займёт пару минут)…")
    command = [sys.executable, str(HERE / "build_exe.py"), "--onedir"]
    if with_subtitles:
        command.append("--with-subtitles")
    if subprocess.run(command, cwd=HERE).returncode != 0:
        raise SystemExit("сборка приложения не удалась")
    if not (folder / EXE_NAME).exists():
        raise SystemExit(f"не найден {folder / EXE_NAME}")
    return folder


def build_zip() -> Path:
    """Единый архив для отправки."""
    archive = DIST / f"Нарезка-Shorts-{APP_VERSION}-portable.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for path in sorted(PACKAGE.rglob("*")):
            if path.is_file():
                package.write(path, path.relative_to(PACKAGE))
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Переносимая сборка для отправки")
    parser.add_argument("--with-subtitles", action="store_true",
                        help="включить распознавание речи (сборка станет ~600 МБ)")
    args = parser.parse_args()

    app_folder = ensure_app_folder(args.with_subtitles)
    shutil.rmtree(PACKAGE, ignore_errors=True)
    PACKAGE.mkdir(parents=True, exist_ok=True)

    print("Собираю комплект…")
    shutil.copytree(app_folder, PACKAGE / APP_FOLDER_NAME)
    install = PACKAGE / "УСТАНОВИТЬ.cmd"
    install.write_text(to_cp866(install_script()), encoding="cp866", newline="\r\n")
    uninstall = PACKAGE / APP_FOLDER_NAME / "УДАЛИТЬ.cmd"
    uninstall.write_text(to_cp866(uninstall_script()), encoding="cp866", newline="\r\n")
    # PowerShell читает .ps1 в своей кодировке, поэтому здесь UTF-8 с BOM —
    # иначе русские сообщения превращаются в мусор.
    shortcuts = PACKAGE / "ЯРЛЫКИ.ps1"
    shortcuts.write_text(SHORTCUT_PS1, encoding="utf-8-sig", newline="\r\n")
    (PACKAGE / "ПРОЧТИ-МЕНЯ.txt").write_text(readme_text(), encoding="utf-8")
    for name in ("ЛИЦЕНЗИИ.md", "ПОЛИТИКА-КОНФИДЕНЦИАЛЬНОСТИ.md"):
        source = HERE / "store" / name
        if source.exists():
            shutil.copy2(source, PACKAGE / name)

    archive = build_zip()
    size = archive.stat().st_size / 1e6
    print(f"\nГотово: {archive} ({size:.0f} МБ)")
    print("Отправляйте этот архив: получатель распаковывает и запускает «УСТАНОВИТЬ.cmd».")
    if not args.with_subtitles:
        print("Субтитры в этой сборке недоступны (модуль распознавания не включён).\n"
              "Нужна полная сборка с субтитрами: make_portable.py --with-subtitles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
