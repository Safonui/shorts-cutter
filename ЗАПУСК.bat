@echo off
chcp 866 >nul
setlocal
cd /d "%~dp0"

rem Запуск приложения "Нарезка Shorts".
rem Интерпретатор выбирается по порядку: окружение проекта .venv,
rem затем системный Python через launcher py, затем python из PATH.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if not defined PY (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo.
    echo Не найден работающий Python.
    echo Установите Python 3.11 или новее с сайта python.org и отметьте
    echo при установке галочки "Add python.exe to PATH" и "tcl/tk and IDLE".
    echo.
    pause
    exit /b 1
)

rem Интернет нужен один раз: пакет imageio-ffmpeg приносит внутрь себя ffmpeg.
%PY% -c "import imageio_ffmpeg" >nul 2>&1
if errorlevel 1 (
    echo Устанавливаю зависимости...
    %PY% -m pip install -r requirements.txt
)

%PY% main.py
if errorlevel 1 pause
