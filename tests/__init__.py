# -*- coding: utf-8 -*-
"""Тесты приложения «Нарезка Shorts» (запуск: python main.py --selftest)."""

from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

__all__ = ["remove_tree"]


def _clear_readonly(root: Path) -> None:
    """Снять атрибут «только чтение» у каталога и всего, что внутри.

    OneDrive помечает каталоги атрибутом «только чтение», и тогда обычное
    удаление папки отвечает «отказано в доступе» (WinError 5), хотя папка пуста.
    """
    for path in [root, *root.rglob("*")]:
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass


def remove_tree(path: Path | str, attempts: int = 4, pause: float = 0.25) -> None:
    """Убрать временный каталог вместе с содержимым, не падая на ошибке.

    С первого раза может не получиться: каталог бывает ещё занят сразу после
    удаления файлов, а в папках OneDrive у него стоит атрибут «только чтение».
    Поэтому чистим атрибуты и пробуем снова — иначе пустые папки остаются
    в папке с видео (именно так и было раньше: ошибку глушил ignore_errors).
    """
    target = Path(path)
    for attempt in range(1, attempts + 1):
        if not target.exists():
            return
        if attempt > 1:
            _clear_readonly(target)
        try:
            shutil.rmtree(target)
            return
        except OSError:
            if attempt == attempts:
                return
            time.sleep(pause)
