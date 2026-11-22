# -*- coding: utf-8 -*-
"""Точка входа приложения «Нарезка Shorts».

Запуск:  python main.py
Самопроверка парсера интервалов (без интерфейса):  python main.py --selftest
Отчёт об окружении:                              python main.py --check
Проверка распознавания речи (для сборки с субтитрами): --check-subtitles
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


def run_check_subtitles() -> int:
    """Проверить распознавание речи внутри собранной программы.

    Запуск: <программа> --check-subtitles
    Нужна сборке в облаке: она доказывает, что в .exe попали не только файлы
    модуля, но и его библиотеки (ctranslate2, onnxruntime, av), что модель
    загружается и что распознавание действительно идёт, а не падает на первом
    же вызове. Возвращает 0, только если прошли все шаги.
    """
    import subprocess
    import tempfile
    import time

    from applog import get_logger, log_command, setup_logging

    setup_logging()
    log = get_logger()
    lines: list[str] = ["=== Проверка распознавания речи (субтитры) ==="]
    status = 0

    try:
        import av
        import ctranslate2
        import faster_whisper
        import onnxruntime

        lines.append(f"faster-whisper: {faster_whisper.__version__}")
        lines.append(f"ctranslate2:    {ctranslate2.__version__}")
        lines.append(f"onnxruntime:    {onnxruntime.__version__}")
        lines.append(f"av:             {av.__version__}")
    except Exception as exc:            # noqa: BLE001 — в отчёте нужен любой сбой
        lines.append(f"ОШИБКА импорта модулей распознавания: {type(exc).__name__}: {exc}")
        status = 1

    # Короткий звук для распознавания делаем вшитым ffmpeg: так проверяется
    # ещё и он, а на выходе получается обычный WAV 16 кГц моно.
    wav = Path(tempfile.gettempdir()) / "shorts_check_subtitles.wav"
    if status == 0:
        try:
            from cutting import CREATE_NO_WINDOW, ffmpeg_exe

            command = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                       "-ar", "16000", "-ac", "1", "-y", str(wav)]
            log_command(command)
            result = subprocess.run(command, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace",
                                    creationflags=CREATE_NO_WINDOW, timeout=180)
            log.info("Код возврата: %s", result.returncode)
            if result.returncode != 0 or not wav.exists():
                raise RuntimeError(result.stderr.strip() or "ffmpeg не создал файл")
            lines.append(f"звук:           {wav.name}, {wav.stat().st_size} байт")
        except Exception as exc:        # noqa: BLE001
            lines.append(f"ОШИБКА подготовки звука: {type(exc).__name__}: {exc}")
            status = 1

    if status == 0:
        try:
            from faster_whisper import WhisperModel

            from subtitles import local_model_path, read_wav_mono

            model_path = local_model_path()
            lines.append(f"модель:         {model_path}")

            started = time.time()
            model = WhisperModel(model_path, device="cpu", compute_type="int8")
            loaded = time.time() - started

            # Путь распознавания ровно как в приложении: звук читаем сами
            # (av не участвует — в av 19 убран параметр, на котором падает
            # faster-whisper), а vad_filter проверяет ещё и onnxruntime.
            started = time.time()
            audio = read_wav_mono(wav)
            segments, _info = model.transcribe(audio, beam_size=5, vad_filter=True)
            texts = [segment.text.strip() for segment in segments]
            elapsed = time.time() - started

            lines.append(f"модель загружена за {loaded:.1f} с")
            lines.append(f"распознано: сегментов {len(texts)}, за {elapsed:.1f} с")
            lines.append(f"текст: {' '.join(texts)[:120] or '(тишина — для этого звука так и надо)'}")
        except Exception as exc:        # noqa: BLE001
            lines.append(f"ОШИБКА распознавания: {type(exc).__name__}: {exc}")
            status = 1

    try:
        wav.unlink()
    except OSError:
        pass

    lines.append("=== Проверка субтитров завершена ===")
    for line in lines:
        log.info("%s", line)
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
    if "--check-subtitles" in sys.argv:
        sys.exit(run_check_subtitles())
    sys.exit(main())
