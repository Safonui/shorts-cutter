# -*- coding: utf-8 -*-
"""Модальные окна сообщений в тёмной теме.

Системные messagebox в Windows светлые, поэтому для единого оформления
(п. 3.6 ТЗ) сообщения показываются собственным диалогом. Если создать окно
почему-то нельзя, используется обычный messagebox — лишь бы пользователь
увидел понятный текст ошибки на русском (п. 3.7 ТЗ).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from theme import COLORS, FONTS, apply_dark_titlebar, track_dark_titlebar

__all__ = ["show_message", "show_error", "ask_yes_no", "ask_ok_cancel", "ask_open_folder",
           "ask_choice"]

_KIND_ACCENT = {
    "info": COLORS["accent"],
    "error": COLORS["accent_hover"],
    "question": COLORS["accent"],
}
_KIND_TITLE = {
    "info": "Сообщение",
    "error": "Ошибка",
    "question": "Подтверждение",
}


class _DarkDialog(tk.Toplevel):
    """Простое модальное окно: заголовок, текст, кнопки."""

    def __init__(self, parent: tk.Misc | None, title: str, text: str, kind: str,
                 buttons: tuple[tuple[str, object, str, bool], ...],
                 cancel_value: object = None) -> None:
        super().__init__(parent)
        self.result: object = cancel_value
        self._cancel_value = cancel_value

        self.withdraw()
        self.title(title or _KIND_TITLE.get(kind, "Сообщение"))
        self.configure(background=COLORS["bg"])
        self.resizable(False, False)
        if parent is not None:
            try:
                self.transient(parent)
            except tk.TclError:
                pass

        body = ttk.Frame(self, style="Panel.TFrame", padding=18)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Label(body, text=title or _KIND_TITLE.get(kind, "Сообщение"),
                  style="Panel.TLabel", font=FONTS.get("section", ("Segoe UI", 11, "bold")),
                  foreground=_KIND_ACCENT.get(kind, COLORS["accent"])).pack(anchor="w")
        ttk.Label(body, text=text, style="Panel.TLabel", wraplength=560,
                  justify="left").pack(anchor="w", pady=(10, 18))

        row = ttk.Frame(body, style="Panel.TFrame")
        row.pack(anchor="e")
        default_button: ttk.Button | None = None
        for column, (label, value, style, is_default) in enumerate(buttons):
            button = ttk.Button(row, text=label, style=style,
                                command=lambda v=value: self._close(v))
            button.grid(row=0, column=column, padx=(8, 0))
            if is_default:
                default_button = button

        self.bind("<Return>", lambda _e: self._close(self._default_value(buttons)))
        self.bind("<Escape>", lambda _e: self._close(self._cancel_value))
        if default_button is not None:
            default_button.focus_set()

        self._center(parent)
        self.deiconify()
        apply_dark_titlebar(self)
        track_dark_titlebar(self)
        try:
            self.grab_set()
        except tk.TclError:
            pass

    @staticmethod
    def _default_value(buttons: tuple[tuple[str, object, str, bool], ...]) -> object:
        for _label, value, _style, is_default in buttons:
            if is_default:
                return value
        return None

    def _center(self, parent: tk.Misc | None) -> None:
        self.update_idletasks()
        width, height = self.winfo_width(), self.winfo_height()
        try:
            if parent is not None and parent.winfo_exists():
                x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
                y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
            else:
                x = (self.winfo_screenwidth() - width) // 2
                y = (self.winfo_screenheight() - height) // 3
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _close(self, value: object) -> None:
        self.result = value
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def _dialog(parent: tk.Misc | None, title: str, text: str, kind: str = "info",
            buttons: tuple[tuple[str, object, str, bool], ...] = (
                ("Хорошо", True, "Accent.TButton", True),),
            cancel_value: object = None) -> object:
    if parent is None:
        parent = tk._default_root  # type: ignore[attr-defined]
    try:
        dialog = _DarkDialog(parent, title, text, kind, buttons, cancel_value)
        dialog.wait_window()
        return dialog.result
    except tk.TclError:
        # Совсем крайний случай — системный диалог, но текст ошибки виден.
        messagebox.showinfo(title or _KIND_TITLE.get(kind, "Сообщение"), text)
        return cancel_value


def show_message(parent: tk.Misc | None, title: str, text: str) -> None:
    """Информационное сообщение."""
    _dialog(parent, title, text, "info")


def show_error(parent: tk.Misc | None, title: str, text: str) -> None:
    """Сообщение об ошибке (без traceback на экране)."""
    _dialog(parent, title, text, "error")


def ask_yes_no(parent: tk.Misc | None, title: str, text: str,
               yes_label: str = "Да", no_label: str = "Отмена") -> bool:
    """Подтверждение: True — пользователь согласился."""
    result = _dialog(
        parent, title, text, "question",
        buttons=((no_label, False, "TButton", False),
                 (yes_label, True, "Accent.TButton", True)),
        cancel_value=False)
    return bool(result)


def ask_ok_cancel(parent: tk.Misc | None, title: str, text: str) -> bool:
    """То же, что ask_yes_no, но с подписью «ОК»."""
    return ask_yes_no(parent, title, text, yes_label="ОК")


def ask_open_folder(parent: tk.Misc | None, title: str, text: str) -> bool:
    """Окно с результатами нарезки: True — открыть папку с клипами.

    Пункт 3.4 ТЗ: по завершении показывается список готовых файлов и кнопка
    «Открыть папку» (открывает её вызывающий код через os.startfile).
    """
    result = _dialog(
        parent, title, text, "info",
        buttons=(("Закрыть", False, "TButton", False),
                 ("Открыть папку", True, "Accent.TButton", True)),
        cancel_value=False)
    return bool(result)


def ask_choice(parent: tk.Misc | None, title: str, text: str,
               options: tuple[tuple[str, object], ...],
               cancel_label: str = "Отмена") -> object | None:
    """Выбор из нескольких вариантов: значение выбранного или None.

    Первый вариант — по умолчанию (акцентная кнопка и фокус).
    """
    buttons = tuple(
        (label, value, "Accent.TButton" if index == 0 else "TButton", index == 0)
        for index, (label, value) in enumerate(options)
    ) + ((cancel_label, None, "TButton", False),)
    return _dialog(parent, title, text, "question", buttons=buttons, cancel_value=None)
