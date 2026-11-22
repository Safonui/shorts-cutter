# -*- coding: utf-8 -*-
"""Точка входа приложения «Нарезка Shorts».

Запуск:  python main.py
Самопроверка парсера интервалов (без интерфейса):  python main.py --selftest
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def run_selftest() -> int:
    """Прогнать тесты парсера и проверки интервалов (критерий приёмки № 2)."""
    import unittest

    loader = unittest.TestLoader()
    suite = loader.discover(str(APP_DIR / "tests"), pattern="test_*.py", top_level_dir=str(APP_DIR))
    if suite.countTestCases() == 0:
        print("Тесты не найдены: нет файлов tests/test_*.py")
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def run_check() -> int:
    """Короткий отчёт об окружении: python main.py --check (или .exe --check).

    Нужен для поддержки: сразу видно, собран ли .exe, где ffmpeg и доступны ли
    субтитры. Отчёт печатается и дублируется в shorts_cutter.log — у оконной
    сборки .exe консоли нет, и без журнала результат было бы не увидеть.
    Возвращает 0, если ffmpeg на месте.
    """
    import platform

    from applog import get_logger, log_path, setup_logging

    setup_logging()
    lines: list[str] = []
    frozen = getattr(sys, "frozen", False)
    lines.append("=== Проверка окружения «Нарезка Shorts» ===")
    lines.append(f"сборка:      {'да (.exe)' if frozen else 'нет, запуск из исходников'}")
    lines.append(f"Python:      {platform.python_version()} ({sys.executable})")
    try:
        import tkinter
        lines.append(f"tkinter:     есть, Tk {tkinter.TkVersion}")
    except ImportError as exc:
        lines.append(f"tkinter:     НЕТ ({exc})")
    lines.append(f"журнал:      {log_path()}")

    status = 0
    try:
        from cutting import ffmpeg_exe, ffprobe_exe
        lines.append(f"ffmpeg:      {ffmpeg_exe()}")
        lines.append(f"ffprobe:     {ffprobe_exe() or 'нет (параметры читаются разбором ffmpeg -i)'}")
    except Exception as exc:            # noqa: BLE001 — в отчёте нужен любой сбой
        lines.append(f"ffmpeg:      ОШИБКА: {exc}")
        status = 1

    try:
        import subtitles
        available = subtitles.is_available()
        lines.append(f"субтитры:    {'доступны' if available else 'недоступны'}")
        if available:
            lines.append(f"модель:      {subtitles.local_model_path()}")
        else:
            lines.append("             "
                         + subtitles.unavailable_reason().replace("\n", " ").strip())
    except Exception as exc:            # noqa: BLE001
        lines.append(f"субтитры:    ОШИБКА проверки: {exc}")

    lines.append("=== Проверка завершена ===")

    logger = get_logger()
    for line in lines:
        logger.info("%s", line)
        try:
            print(line)
        except (OSError, ValueError, AttributeError):
            pass                     # у оконной сборки консоли может не быть
    return status


def main() -> int:
    from applog import get_logger, setup_logging, log_path

    setup_logging()
    log = get_logger()
    log.info("=== Запуск приложения «Нарезка Shorts» (этап 1) ===")

    try:
        import tkinter  # noqa: F401  (проверяем наличие стандартного Tkinter)
    except ImportError:
        message = ("Не найден модуль Tkinter — графический интерфейс недоступен.\n"
                   "Переустановите Python с официального сайта, отметив компонент "
                   "«tcl/tk and IDLE».")
        print(message, file=sys.stderr)
        log.error(message)
        return 2

    try:
        from ui import ShortsCutterApp
        app = ShortsCutterApp()
    except Exception as exc:  # понятное сообщение вместо traceback
        log.exception("Не удалось создать главное окно")
        print(f"Не удалось запустить приложение: {exc}", file=sys.stderr)
        return 1

    try:
        app.mainloop()
    except KeyboardInterrupt:
        log.info("Прервано пользователем (Ctrl+C)")
    except Exception:
        log.exception("Аварийное завершение работы")
        return 1

    log.info("Приложение закрыто. Журнал: %s", log_path())
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())
    if "--check" in sys.argv:
        sys.exit(run_check())
    sys.exit(main())
