# -*- coding: utf-8 -*-
"""Логирование приложения в файл shorts_cutter.log рядом с программой.

Пункт 3.7 ТЗ: в журнал попадают время, полная команда ffmpeg и код возврата.
Если файл журнала создать нельзя (например, папка только для чтения), приложение
продолжает работать — об этом пишется предупреждение в stderr.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

__all__ = ["LOG_NAME", "app_dir", "log_path", "setup_logging", "get_logger",
           "log_command", "last_log_line", "read_log_tail"]

LOG_NAME = "shorts_cutter.log"
LOGGER_NAME = "shorts"

_configured = False


def app_dir() -> Path:
    """Папка программы: рядом с .exe (если собран PyInstaller) или с main.py."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def log_path() -> Path:
    return app_dir() / LOG_NAME


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Настроить журнал (один раз за запуск) и вернуть логгер приложения."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                  datefmt="%Y-%m-%d %H:%M:%S")

    try:
        file_handler = logging.FileHandler(log_path(), encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)
    except OSError as exc:
        print(f"Не удалось открыть журнал {log_path()}: {exc}", file=sys.stderr)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.WARNING)
    logger.addHandler(stream_handler)

    _configured = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_command(command: list[str], returncode: int | None = None) -> None:
    """Записать в журнал команду целиком и (если известен) код возврата."""
    logger = get_logger()
    try:
        text = subprocess.list2cmdline(command)
    except Exception:
        text = " ".join(str(part) for part in command)
    logger.info("Команда: %s", text)
    if returncode is not None:
        logger.info("Код возврата: %s", returncode)


def read_log_tail(lines: int = 25) -> list[str]:
    """Последние строки журнала (для окна «Журнал работы»)."""
    try:
        content = log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return content.splitlines()[-lines:]


def last_log_line(default: str = "—") -> str:
    """Последняя строка журнала — показывается в статусной строке окна."""
    tail = read_log_tail(1)
    return tail[0] if tail else default
