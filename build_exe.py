# -*- coding: utf-8 -*-
"""Сборка «Нарезки Shorts» в один .exe (PyInstaller).

Запуск:  .venv\\Scripts\\python.exe build_exe.py [--with-subtitles]

Что попадает в сборку:
* само приложение и tkinter;
* ffmpeg из пакета imageio-ffmpeg — поэтому отдельно ставить ffmpeg не нужно.

По умолчанию субтитры в .exe НЕ входят: faster-whisper тянет ctranslate2,
onnxruntime и av, из-за чего сборка вырастает с ~40 МБ до ~600 МБ. Без него
приложение работает полностью, кроме распознавания речи, и в настройках честно
пишет об этом. Ключ --with-subtitles включает распознавание в сборку.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_NAME = "Нарезка Shorts"
DIST = HERE / "dist"
BUILD = HERE / "build"
SUBTITLE_MODULES = ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers",
                    "av", "numpy", "huggingface_hub")


def check_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Не установлен PyInstaller. Выполните:\n"
              "    .venv\\Scripts\\python.exe -m pip install pyinstaller")
        raise SystemExit(1)


def write_version_file(path: Path) -> None:
    """Файл ресурсов версии для PyInstaller: издатель, версия, авторские права."""
    from appconfig import APP_NAME, APP_PUBLISHER, APP_VERSION, APP_YEAR

    numbers = APP_VERSION.split(".")
    while len(numbers) < 4:
        numbers.append("0")
    version_tuple = ", ".join(numbers[:4])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({version_tuple}), prodvers=({version_tuple}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('041904B0', [
        StringStruct('CompanyName', '{APP_PUBLISHER}'),
        StringStruct('FileDescription', '{APP_NAME} — вертикальные клипы 9:16'),
        StringStruct('FileVersion', '{APP_VERSION}'),
        StringStruct('InternalName', 'shorts-cutter'),
        StringStruct('LegalCopyright', '© {APP_YEAR} {APP_PUBLISHER}'),
        StringStruct('OriginalFilename', '{APP_NAME}.exe'),
        StringStruct('ProductName', '{APP_NAME}'),
        StringStruct('ProductVersion', '{APP_VERSION}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1049, 1200])])
  ]
)
""", encoding="utf-8")
    print(f"  ресурсы версии: {path.name} ({APP_NAME} {APP_VERSION})")


def remove_tree(path: Path, attempts: int = 4, pause: float = 0.25) -> None:
    """Убрать каталог или файл прошлой сборки, сняв атрибут «только чтение».

    В папках OneDrive у каталогов стоит атрибут «только чтение», и удаление
    отвечает «отказано в доступе» (WinError 5). Раньше ошибку глушил
    ignore_errors, каталог оставался — и PyInstaller обрывался на нём с
    PermissionError вместо того, чтобы собрать программу.
    """
    target = Path(path)
    if not target.exists():
        return
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            items = [target, *target.rglob("*")] if target.is_dir() else [target]
            for item in items:
                try:
                    os.chmod(item, stat.S_IWRITE)
                except OSError:
                    pass
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return
        except OSError as exc:
            if attempt == attempts:
                print(f"  {target} удалить не удалось: {exc}")
                return
            time.sleep(pause)


def build(with_subtitles: bool, onedir: bool = False, console: bool = False,
          name: str = APP_NAME) -> int:
    check_pyinstaller()
    remove_tree(BUILD)
    DIST.mkdir(exist_ok=True)
    # Папку (или файл) прошлой сборки убираем сами: PyInstaller не умеет снимать
    # атрибут «только чтение», который OneDrive ставит на каталоги, и падает на
    # очистке _internal с PermissionError.
    remove_tree(DIST / name if onedir else DIST / f"{name}.exe")
    version_file = BUILD / "version_info.txt"
    write_version_file(version_file)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir" if onedir else "--onefile",
        "--console" if console else "--windowed",
        "--name", name,
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        "--version-file", str(version_file),
        # Данные imageio-ffmpeg: внутри пакета лежит сам бинарник ffmpeg.
        "--collect-all", "imageio_ffmpeg",
    ]
    icon = HERE / "assets" / "icon.ico"
    if icon.exists():
        command += ["--icon", str(icon), "--add-data", f"{icon};assets"]
    else:
        print("  иконки нет — соберу без неё (запустите make_icon.py)")
    if not with_subtitles:
        for module in SUBTITLE_MODULES:
            command += ["--exclude-module", module]
    else:
        command += ["--collect-all", "faster_whisper"]

    command.append(str(HERE / "main.py"))
    print("Собираю:", " ".join(command[1:6]), "…")
    result = subprocess.run(command, cwd=HERE)
    if result.returncode != 0:
        print("Сборка не удалась, код", result.returncode)
        return result.returncode

    exe = (DIST / name / f"{name}.exe") if onedir else (DIST / f"{name}.exe")
    if not exe.exists():
        print("Готовый файл не найден:", exe)
        return 1
    size = sum(item.stat().st_size for item in exe.parent.rglob("*") if item.is_file())
    print(f"\nГотово: {exe} ({size / 1e6:.0f} МБ со всеми файлами)")
    print("Проверка сборки:")
    try:
        check = subprocess.run([str(exe), "--check"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        print(check.stdout.strip() or check.stderr.strip()[-500:]
              or f"вывод пуст, код {check.returncode} (отчёт всё равно в shorts_cutter.log)")
    except OSError as exc:
        # Чаще всего это Smart App Control: он не пускает неподписанные сборки.
        # Сама сборка при этом готова, проверить её можно на целевой машине.
        print(f"запустить собранный файл не удалось: {exc}")
        print("  Это не ошибка сборки. Если Windows блокирует неподписанные\n"
              "  программы (Smart App Control), проверьте файл на другом компьютере.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка .exe для «Нарезки Shorts»")
    parser.add_argument("--with-subtitles", action="store_true",
                        help="включить в сборку распознавание речи (+~550 МБ)")
    parser.add_argument("--onedir", action="store_true",
                        help="собрать папку с .exe внутри (запуск без распаковки)")
    parser.add_argument("--console", action="store_true",
                        help="с консолью — удобно для отладки")
    parser.add_argument("--name", default=APP_NAME, help="имя сборки")
    args = parser.parse_args()
    return build(args.with_subtitles, args.onedir, args.console, args.name)


if __name__ == "__main__":
    sys.exit(main())
