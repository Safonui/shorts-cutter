# -*- coding: utf-8 -*-
"""Настройки приложения между запусками.

Хранятся в `%APPDATA%\\ShortsCutter\\settings.json` (рядом с профилем
пользователя, а не в папке программы — так работают обычные приложения).
Если файл повреждён или отсутствует, берутся значения по умолчанию: приложение
не должно падать из-за настроек.

В .exe-сборке и при запуске из одного файла путь тот же, поэтому настройки
переносятся между способами запуска.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

__all__ = ["APP_NAME", "APP_VERSION", "APP_PUBLISHER", "APP_YEAR", "SETTING_DEFAULTS",
           "config_path", "load_settings", "save_settings", "reset_settings"]

log = logging.getLogger("shorts")

APP_NAME = "Нарезка Shorts"
APP_VERSION = "1.0.4"
APP_PUBLISHER = "Нарезка Shorts"
APP_YEAR = "2026"
APP_ID = "ShortsCutter"

# Значения по умолчанию и одновременно список сохраняемых ключей.
SETTING_DEFAULTS: dict[str, object] = {
    "format": "Вертикаль 9:16 (1080×1920)",
    "crf": "20",
    "output_dir": "",
    "output_dir_custom": False,
    "chunk_len": "60",
    "split_len": "300",
    "highlight_count": "5",
    "subtitles": 0,
    "font_size": "48",
    "sub_position": "низ",
    "sub_color": "белый",
    "last_dir": "",
    "geometry": "",
}


def config_path() -> Path:
    """Путь к файлу настроек."""
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_ID / "settings.json"


def load_settings() -> dict:
    """Прочитать настройки; ошибки чтения не должны мешать запуску."""
    values = dict(SETTING_DEFAULTS)
    path = config_path()
    if not path.exists():
        return values
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Не удалось прочитать настройки (%s): %s", path, exc)
        return values
    if not isinstance(data, dict):
        log.warning("Файл настроек неожиданного вида, беру значения по умолчанию")
        return values
    for key, default in SETTING_DEFAULTS.items():
        if key in data and isinstance(data[key], type(default)):
            values[key] = data[key]
        elif key in data and isinstance(default, bool):
            values[key] = bool(data[key])
    log.info("Настройки загружены: %s", path)
    return values


def save_settings(values: dict) -> bool:
    """Записать настройки (только известные ключи). Тихий отказ при ошибке.

    Запись идёт «через замену»: сначала временный файл, потом переименование.
    Если система ненадолго заняла файл (антивирус, синхронизация), пробуем ещё
    раз — иначе настройки молча не сохранятся.
    """
    payload = {key: values[key] for key in SETTING_DEFAULTS if key in values}
    path = config_path()
    for attempt in (1, 2):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)               # запись «через замену»: файл не побьётся
            return True
        except OSError as exc:
            if attempt == 2:
                log.warning("Не удалось сохранить настройки (%s): %s", path, exc)
                return False
            time.sleep(0.2)
    return False


def reset_settings() -> bool:
    """Сбросить настройки в значения по умолчанию."""
    return save_settings(dict(SETTING_DEFAULTS))
