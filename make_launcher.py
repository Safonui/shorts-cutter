# -*- coding: utf-8 -*-
"""Сборка одного файла-лаунчера «Нарезка Shorts.cmd».

Зачем: на компьютерах с включённым Smart App Control любой свежесобранный
неподписанный .exe блокируется («did not meet the Enterprise signing level
requirements») — проверено по журналу CodeIntegrity. Командный файл — обычный
текст, такая политика его не трогает. Внутри файла лежит zip со всем
приложением; при запуске он распаковывается рядом в папку `_app` и запускается
уже установленным Python (Python и его файлы политика пропускает).

Запуск сборки:  .venv\\Scripts\\python.exe make_launcher.py
"""

from __future__ import annotations

import base64
import hashlib
import io
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "Нарезка Shorts.cmd"
MARKER = "APP-PAYLOAD-BEGIN"          # только ASCII: по нему режем файл
MODULES = ("main.py", "ui.py", "theme.py", "intervals.py", "cutting.py",
           "subtitles.py", "highlights.py", "dialogs.py", "applog.py",
           "appconfig.py", "taskbar.py", "requirements.txt", "README.md")
# Ресурсы приложения (иконка окна) кладём в архив рядом с модулями.
RESOURCES = (("assets/icon.ico", "assets/icon.ico"),
             ("assets/icon-256.png", "assets/icon-256.png"))

UNPACK_CODE = (
    "import base64,sys,zipfile;"
    "raw=open(sys.argv[1],'rb').read();"
    # rindex: строка-маркер встречается ещё и в этом коде, нужен последний вход
    "payload=b''.join(raw[raw.rindex(b'{marker}'):].splitlines()[1:]);"
    "open(sys.argv[2],'wb').write(base64.b64decode(payload));"
    "zipfile.ZipFile(sys.argv[2]).extractall(sys.argv[3])"
)


def build_payload() -> tuple[str, str]:
    """Вернуть (base64-zip, отпечаток сборки)."""
    buffer = io.BytesIO()
    names: list[str] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in MODULES:
            path = HERE / name
            if path.exists():
                archive.write(path, name)
                names.append(name)
            else:
                print(f"  нет файла {name} — пропускаю")
        for source, target in RESOURCES:
            path = HERE / source
            if path.exists():
                archive.write(path, target)
                names.append(target)
        for test in sorted((HERE / "tests").glob("*.py")):
            archive.write(test, f"tests/{test.name}")
            names.append(f"tests/{test.name}")
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()[:12]
    print(f"  в архиве файлов: {len(names)}, размер: {len(data) / 1024:.0f} КБ, "
          f"отпечаток: {digest}")
    encoded = base64.b64encode(data).decode("ascii")
    # Разбиваем на короткие строки: так файл читаем любым редактором.
    wrapped = "\n".join(encoded[index:index + 76] for index in range(0, len(encoded), 76))
    return wrapped, digest


def build_script(payload: str, digest: str) -> str:
    """Текст .cmd: найти Python, распаковать приложение, запустить, вернуть код."""
    unpack = UNPACK_CODE.format(marker=MARKER)
    return f"""@echo off
chcp 866 >nul
setlocal
title Нарезка Shorts
cd /d "%~dp0"

rem ============================================================
rem  Нарезка Shorts — один запускаемый файл.
rem  Внутри него лежит всё приложение. При первом запуске оно
rem  распаковывается в папку _app рядом с этим файлом и
rem  запускается установленным Python (нужен Python 3.11+).
rem ============================================================

set "BUILD={digest}"
set "APPDIR=%~dp0_app"
set "PYQ="

if exist "%~dp0.venv\\Scripts\\python.exe" set "PYQ="%~dp0.venv\\Scripts\\python.exe""
if not defined PYQ (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYQ=py -3"
)
if not defined PYQ (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYQ=python"
)
if not defined PYQ (
    echo.
    echo Не найден работающий Python.
    echo Установите Python 3.11 или новее с сайта python.org и отметьте
    echo при установке галочки "Add python.exe to PATH" и "tcl/tk and IDLE".
    echo.
    pause
    exit /b 1
)

rem Распаковка приложения: если его нет рядом или сборка в файле новее.
rem Важно: переменные задаются и читаются отдельными строками без блоков ( ),
rem иначе cmd подставляет их значения ещё до выполнения блока.
set "NEED_UNPACK=1"
set "CURRENT="
if exist "%APPDIR%\\build.txt" set /p CURRENT=<"%APPDIR%\\build.txt"
if "%CURRENT%"=="%BUILD%" set "NEED_UNPACK=0"

if "%NEED_UNPACK%"=="1" echo Первый запуск: готовлю приложение...
if "%NEED_UNPACK%"=="1" if exist "%APPDIR%" rd /s /q "%APPDIR%"
if "%NEED_UNPACK%"=="1" mkdir "%APPDIR%" >nul 2>&1
if "%NEED_UNPACK%"=="1" %PYQ% -c "{unpack}" "%~f0" "%APPDIR%\\app.zip" "%APPDIR%"
if "%NEED_UNPACK%"=="1" if not exist "%APPDIR%\\main.py" (
    echo Не удалось распаковать приложение.
    pause
    exit /b 1
)
if "%NEED_UNPACK%"=="1" del "%APPDIR%\\app.zip" >nul 2>&1
if "%NEED_UNPACK%"=="1" echo %BUILD%>"%APPDIR%\\build.txt"

rem Зависимости: интернет нужен один раз.
%PYQ% -c "import imageio_ffmpeg" >nul 2>&1
if errorlevel 1 (
    echo Устанавливаю зависимости...
    %PYQ% -m pip install -r "%APPDIR%\\requirements.txt"
)

%PYQ% "%APPDIR%\\main.py" %*
exit /b %errorlevel%

{MARKER}
{payload}
"""


def to_cp866(text: str) -> str:
    """Заменить символы, которых нет в cp866 (её читает cmd.exe для русского).

    Длинное тире, многоточие и стрелки в cp866 отсутствуют — заменяем на ASCII,
    иначе файл просто не запишется.
    """
    replacements = {"—": "-", "–": "-", "…": "...", "’": "'", "“": '"', "”": '"',
                    "→": "->", "•": "-", "№": "N"}
    result: list[str] = []
    replaced: set[str] = set()
    for char in text:
        try:
            char.encode("cp866")
            result.append(char)
        except UnicodeEncodeError:
            replaced.add(char)
            result.append(replacements.get(char, "?"))
    if replaced:
        print("  заменены символы, недоступные в cp866: " + " ".join(sorted(replaced)))
    return "".join(result)


def main() -> int:
    print("Собираю один файл-лаунчер…")
    payload, digest = build_payload()
    script = to_cp866(build_script(payload, digest))
    OUTPUT.write_text(script, encoding="cp866", newline="\r\n")
    print(f"Готово: {OUTPUT.name} ({OUTPUT.stat().st_size / 1024:.0f} КБ)")
    print("Проверка: «Нарезка Shorts.cmd --check», обычный запуск — двойной клик.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
