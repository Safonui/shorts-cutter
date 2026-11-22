# -*- coding: utf-8 -*-
"""Прогресс в панели задач Windows (полноценное приложение показывает его там).

Через COM-интерфейс ITaskbarList3: зелёная/жёлтая/красная полоска поверх значка
в панели задач. Всё необязательное: если система или Tkinter не позволяют, класс
тихо ничего не делает и приложение работает как обычно.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import tkinter as tk

__all__ = ["TaskbarProgress", "TBPF_NOPROGRESS", "TBPF_NORMAL", "TBPF_ERROR",
           "TBPF_INDETERMINATE"]

log = logging.getLogger("shorts")

TBPF_NOPROGRESS = 0x0
TBPF_INDETERMINATE = 0x1
TBPF_NORMAL = 0x2
TBPF_ERROR = 0x4
TBPF_PAUSED = 0x8

_CLSID_TaskbarList = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
_IID_ITaskbarList3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEA84}"


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    """Строка вида {..-..} в структуру GUID."""
    value = text.strip("{}").replace("-", "")
    data = bytes.fromhex(value)
    return _GUID(
        int.from_bytes(data[0:4], "little"),
        int.from_bytes(data[4:6], "little"),
        int.from_bytes(data[6:8], "little"),
        (ctypes.c_ubyte * 8)(*data[8:16]),
    )


class TaskbarProgress:
    """Полоска прогресса на значке приложения в панели задач."""

    def __init__(self, window: tk.Misc) -> None:
        self.window = window
        self._taskbar = ctypes.c_void_p()
        self._ok = False
        if sys.platform != "win32":
            return
        try:
            self._ok = self._init_com()
        except Exception as exc:            # noqa: BLE001 — без панели задач жить можно
            log.info("Прогресс в панели задач недоступен: %s", exc)
            self._ok = False

    # -- внутреннее ---------------------------------------------------------
    def _init_com(self) -> bool:
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        clsid, iid = _guid(_CLSID_TaskbarList), _guid(_IID_ITaskbarList3)
        result = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                        ctypes.byref(iid), ctypes.byref(self._taskbar))
        if result != 0 or not self._taskbar:
            return False
        # HrInit — метод с индексом 3 в таблице методов.
        self._call(3)
        return True

    def _call(self, index: int, *args) -> int:
        """Вызвать метод COM по индексу в таблице виртуальных методов."""
        if not self._taskbar:
            return -1
        table = ctypes.cast(self._taskbar,
                            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *[type(a) for a in args])
        return method(table[index])(self._taskbar, *args)

    def _handle(self) -> int | None:
        """Дескриптор окна Tk — то, что нужно панели задач."""
        try:
            self.window.update_idletasks()
            return int(self.window.winfo_id())
        except (tk.TclError, ValueError):
            return None

    # -- публичное ----------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._ok

    def set_state(self, state: int) -> None:
        if not self._ok:
            return
        handle = self._handle()
        if handle is None:
            return
        try:
            # SetProgressState — индекс 10 (после SetProgressValue).
            self._call(10, ctypes.c_void_p(handle), ctypes.c_int(state))
        except Exception:                   # noqa: BLE001
            self._ok = False

    def set_progress(self, value: float, maximum: float = 100.0) -> None:
        """Показать прогресс 0…maximum; при нуле полоска убирается."""
        if not self._ok:
            return
        if value <= 0:
            self.set_state(TBPF_NOPROGRESS)
            return
        handle = self._handle()
        if handle is None:
            return
        try:
            self.set_state(TBPF_NORMAL)
            # SetProgressValue — индекс 9.
            self._call(9, ctypes.c_void_p(handle), ctypes.c_ulonglong(int(value)),
                       ctypes.c_ulonglong(int(maximum)))
        except Exception:                   # noqa: BLE001
            self._ok = False

    def busy(self) -> None:
        """Неопределённый прогресс (когда проценты неизвестны)."""
        self.set_state(TBPF_INDETERMINATE)

    def error(self) -> None:
        self.set_state(TBPF_ERROR)

    def clear(self) -> None:
        self.set_state(TBPF_NOPROGRESS)
