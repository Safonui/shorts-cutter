# -*- coding: utf-8 -*-
"""Тёмная тема приложения «Нарезка Shorts».

Палитра взята с референса (тёмно-фиолетовый неон): почти чёрная база, панели
глубокого фиолетового, светящиеся фиолетово-маджентовые акценты. Значения
подобраны по самой картинке — база #050117/#02010E, панель #0D062F,
акценты #8333D7/#B430E5, светлый текст #E0E0F0.

Переменная COLORS_TZ ниже — прежняя палитра из п. 3.6 ТЗ (тёмно-синяя с
тёмно-красным акцентом). Она оставлена, чтобы можно было вернуться к варианту
из технического задания: достаточно присвоить COLORS = dict(COLORS_TZ).

Ни один модуль не должен задавать цвета «на месте» — только через COLORS,
иначе в окне появятся светлые системные элементы.

Основная точка входа — apply_theme(root): она настраивает стили ttk и
заполняет словарь FONTS (шрифт Segoe UI, если он есть в системе).
"""

from __future__ import annotations

import math
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# --- Палитра -----------------------------------------------------------------

COLORS = {
    # фоны — очень тёмные, с фиолетово-синим оттенком
    "bg": "#0A0A14",           # фон окна
    "panel": "#14142A",        # панели HUD
    "panel_alt": "#1A1A2E",    # поля ввода (чуть светлее панелей)
    "panel_dark": "#10101F",   # шапка таблицы, служебная полоса
    "hover": "#23234A",        # подсветка под курсором
    "border": "#2A2A4A",       # обычные рамки и разделители
    # неоновые контуры
    "neon_pink": "#FF2E97",    # главный акцент: кнопки «Нарезать» и «Отмена»
    "neon_pink_hot": "#FF6FC0",  # свечение при наведении
    "neon_cyan": "#00E5FF",    # рамки панелей, заголовки секций, прогресс
    "neon_cyan_dim": "#0B6E80",  # приглушённая голубая рамка
    "neon_violet": "#9D4EDD",  # выделение строк
    # градиент «шапки» и свечение (см. draw_hero_backdrop)
    "hero_top": "#1A1A2E",
    "hero_bottom": "#0A0A14",
    "glow": "#9D4EDD",
    # акценты (имена сохранены: ими пользуются диалоги и кнопки)
    "accent": "#FF2E97",
    "accent_hover": "#FF6FC0",
    "accent_dark": "#B01A68",
    "accent_disabled": "#4A1030",
    "accent_soft": "#00E5FF",
    "on_accent": "#FFFFFF",
    "select": "#9D4EDD",       # фон выбранной строки списка
    # текст
    "text": "#EAEAFF",         # основной — белый
    "text_soft": "#C3D5FF",    # светло-голубой: заголовки секций, значения
    "muted": "#8A8FA8",        # второстепенный серо-голубой
    "disabled_text": "#55597A",
    # статусы
    "error_bg": "#8B0000",     # фон строки с ошибкой (приглушённый красный)
    "error_text": "#FFD9D9",
    "warning_text": "#E8C56A",
    "ok": "#4A7C59",           # «готово» (только для статуса)
    "ok_text": "#8FBF9F",
}

# Прежняя палитра из п. 3.6 ТЗ — для быстрого возврата к варианту задания.
# Ключи те же, что и в COLORS: подмена одним присваиванием не ломает интерфейс.
COLORS_TZ = {
    "bg": "#0D1B2A", "panel": "#1B263B", "panel_alt": "#22314A", "panel_dark": "#152238",
    "hover": "#26374F", "border": "#2E4057",
    "neon_pink": "#A4161A", "neon_pink_hot": "#C21E23", "neon_cyan": "#8FB3D9",
    "neon_cyan_dim": "#3B5470", "neon_violet": "#3E5C76",
    "hero_top": "#16233A", "hero_bottom": "#0D1B2A", "glow": "#1B263B",
    "accent": "#A4161A", "accent_hover": "#C21E23", "accent_dark": "#8B0000",
    "accent_disabled": "#4A1418", "accent_soft": "#A4161A", "on_accent": "#FFFFFF",
    "select": "#A4161A",
    "text": "#E0E1DD", "text_soft": "#CFD8E3", "muted": "#A9B4C2",
    "disabled_text": "#5C6B7A",
    "error_bg": "#5A1015", "error_text": "#FFC9C9", "warning_text": "#E8C56A",
    "ok": "#4A7C59", "ok_text": "#8FBF9F",
}

# Заполняется в apply_theme(): имя шрифта зависит от того, что есть в системе.
FONTS: dict[str, tuple] = {}


def blend(color_a: str, color_b: str, ratio: float) -> str:
    """Смешать два цвета: ratio 0 — color_a, 1 — color_b."""
    ratio = max(0.0, min(1.0, ratio))
    parts = []
    for offset in (1, 3, 5):
        left = int(color_a[offset:offset + 2], 16)
        right = int(color_b[offset:offset + 2], 16)
        parts.append(round(left + (right - left) * ratio))
    return "#{:02X}{:02X}{:02X}".format(*parts)


def draw_vertical_gradient(canvas: tk.Canvas, top: str, bottom: str,
                           width: int, height: int) -> None:
    """Простой вертикальный градиент (используется для тонких полос)."""
    canvas.delete("gradient")
    if width <= 0 or height <= 0:
        return
    for y in range(height):
        color = blend(top, bottom, y / max(1, height - 1))
        canvas.create_line(0, y, width, y, fill=color, tags="gradient")


def draw_hero_backdrop(canvas: tk.Canvas, width: int, height: int, columns: int = 28,
                       tag: str = "gradient") -> None:
    """Фон шапки: вертикальный градиент плюс мягкое радиальное свечение.

    Считаем цвет по сетке (columns × высота) и рисуем короткими линиями:
    так получается и перепад «фиолетовый → чёрный», и световое пятно,
    как на референсе. У Tkinter нет ни градиентов, ни прозрачности, поэтому
    цвет каждой клетки смешивается вручную функцией blend().
    """
    canvas.delete(tag)
    if width <= 1 or height <= 1:
        return

    top, bottom = COLORS["hero_top"], COLORS["hero_bottom"]
    glow = COLORS["glow"]
    center_x, center_y = 0.30, 0.42      # где светит
    radius_x, radius_y = 0.62, 0.95      # насколько широко
    peak = 0.55                          # сила свечения в центре
    step = max(1, width // max(1, columns))

    for y in range(height):
        ratio = y / max(1, height - 1)
        base = blend(top, bottom, ratio)
        dy = (ratio - center_y) / radius_y
        row_limit = None
        for x in range(0, width, step):
            dx = (x / max(1, width - 1) - center_x) / radius_x
            strength = peak * math.exp(-(dx * dx + dy * dy))
            color = blend(base, glow, strength)
            if color == row_limit:
                continue                     # не рисуем одинаковые соседние клетки
            row_limit = color
            canvas.create_line(x, y, min(width, x + step), y, fill=color, tags=tag)


def draw_glow(canvas: tk.Canvas, center_x: int, center_y: int, radius: int,
              color: str, base: str, steps: int = 10, tag: str = "glow") -> None:
    """Мягкое свечение овалами — простой вариант для небольших элементов."""
    canvas.delete(tag)
    for step in range(steps, 0, -1):
        ratio = (step / steps) ** 2 * 0.35
        size = radius * step / steps
        canvas.create_oval(center_x - size, center_y - size * 0.55,
                           center_x + size, center_y + size * 0.55,
                           fill=blend(base, color, ratio), outline="", tags=tag)


def make_hud_panel(parent: tk.Misc, border_color: str, border_width: int = 2,
                   padding: tuple[int, int] = (14, 12),
                   style: str = "Panel.TFrame") -> tuple[tk.Frame, ttk.Frame]:
    """Собрать HUD-панель: тёмный блок в тонкой светящейся неоновой рамке.

    Рамка сделана внешним tk.Frame неонового цвета: у ttk-рамок нет надёжного
    способа задать цвет границы. Возвращает (внешний_контейнер, внутренняя_рамка).
    """
    outer = tk.Frame(parent, background=border_color, bd=0, highlightthickness=0)
    inner = ttk.Frame(outer, style=style, padding=padding)
    inner.pack(fill="both", expand=True, padx=border_width, pady=border_width)
    return outer, inner


def _pick_family(root: tk.Misc, candidates: tuple[str, ...]) -> str:
    """Вернуть первое доступное семейство шрифтов из списка кандидатов."""
    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        available = set()
    for name in candidates:
        if name in available:
            return name
    try:
        return tkfont.nametofont("TkDefaultFont").actual("family")
    except tk.TclError:
        return candidates[-1]


def apply_theme(root: tk.Misc) -> dict[str, tuple]:
    """Применить тёмную тему к окну приложения. Возвращает словарь шрифтов."""
    family = _pick_family(root, ("Segoe UI", "Segoe UI Variable Text", "Tahoma", "Arial"))
    mono = _pick_family(root, ("Consolas", "Cascadia Mono", "Courier New"))

    FONTS.clear()
    FONTS.update({
        "base": (family, 10),
        "small": (family, 9),
        "bold": (family, 10, "bold"),
        "title": (family, 16, "bold"),
        "hero": (family, 21, "bold"),
        "subtitle": (family, 10),
        "section": (family, 11, "bold"),
        "button": (family, 10),
        "neon_button": (family, 11, "bold"),
        # Моноширинный шрифт для таймкодов: колонки должны выравниваться.
        "mono": (mono, 10),
        "mono_small": (mono, 9),
        "mono_bold": (mono, 10, "bold"),
    })

    try:
        root.configure(background=COLORS["bg"])
    except tk.TclError:
        pass

    _configure_option_db(root)
    _configure_ttk(root)
    apply_dark_titlebar(root)
    track_dark_titlebar(root)
    return FONTS


def apply_dark_titlebar(window: tk.Misc) -> bool:
    """Сделать системный заголовок окна тёмным (Windows 10 1809+).

    Без этого окно приложения тёмное, а полоса заголовка остаётся светлой —
    как раз то, чего требует избегать п. 3.6 ТЗ. Если система вызов не
    поддерживает, тихо возвращаем False: ошибка оформления не должна мешать работе.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        window.update_idletasks()
        handle = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # Win11, затем Win10
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(handle), ctypes.c_uint(attribute),
                ctypes.byref(value), ctypes.sizeof(value))
            if result == 0:
                return True
    except Exception:  # нет dwmapi, нет прав, нестандартная тема — не критично
        pass
    return False


def track_dark_titlebar(window: tk.Misc) -> None:
    """Повторять затемнение заголовка при каждом показе окна."""
    window.bind("<Map>", lambda _event: apply_dark_titlebar(window), add="+")


def _configure_option_db(root: tk.Misc) -> None:
    """Цвета для «не-ttk» элементов: выпадающие списки Combobox и пр."""
    root.option_add("*Background", COLORS["bg"])
    root.option_add("*Foreground", COLORS["text"])
    root.option_add("*TCombobox*Listbox.background", COLORS["panel"])
    root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", COLORS["on_accent"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)


def _configure_ttk(root: tk.Misc) -> None:
    """Настроить все используемые стили ttk под тёмную тему."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # единственная тема, которая красится целиком
    except tk.TclError:
        pass

    bg, panel, panel_alt = COLORS["bg"], COLORS["panel"], COLORS["panel_alt"]
    panel_dark = COLORS["panel_dark"]

    # --- контейнеры и подписи -------------------------------------------------
    style.configure("TFrame", background=bg)
    style.configure("Panel.TFrame", background=panel)
    style.configure("Bar.TFrame", background=panel_dark)

    style.configure("TLabel", background=bg, foreground=COLORS["text"], font=FONTS["base"])
    style.configure("Panel.TLabel", background=panel, foreground=COLORS["text"], font=FONTS["base"])
    style.configure("Title.TLabel", background=bg, foreground=COLORS["text"], font=FONTS["title"])
    style.configure("Subtitle.TLabel", background=bg, foreground=COLORS["muted"], font=FONTS["subtitle"])
    # Заголовки секций: светло-голубые, капсом (текст задаётся в uppercase).
    style.configure("Section.TLabel", background=bg, foreground=COLORS["neon_cyan"],
                    font=FONTS["section"])
    style.configure("Muted.TLabel", background=bg, foreground=COLORS["muted"], font=FONTS["base"])
    style.configure("MutedPanel.TLabel", background=panel, foreground=COLORS["muted"], font=FONTS["base"])
    style.configure("Caption.TLabel", background=panel, foreground=COLORS["muted"], font=FONTS["small"])
    style.configure("Value.TLabel", background=panel, foreground=COLORS["text_soft"], font=FONTS["bold"])
    style.configure("ValueMono.TLabel", background=panel, foreground=COLORS["text_soft"],
                    font=FONTS["mono_bold"])
    style.configure("Hint.TLabel", background=bg, foreground=COLORS["muted"], font=FONTS["small"])
    style.configure("HintPanel.TLabel", background=panel, foreground=COLORS["muted"], font=FONTS["small"])
    style.configure("Status.TLabel", background=panel_dark, foreground=COLORS["text_soft"],
                    font=FONTS["mono_small"])
    style.configure("Log.TLabel", background=panel_dark, foreground=COLORS["disabled_text"],
                    font=FONTS["mono_small"])

    style.configure("TLabelframe", background=bg, bordercolor=COLORS["neon_cyan_dim"],
                    lightcolor=bg, darkcolor=bg, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=bg, foreground=COLORS["neon_cyan"],
                    font=FONTS["section"])

    # --- кнопки ---------------------------------------------------------------
    # Обычные кнопки: тёмная база и голубой неоновый контур.
    style.configure("TButton", background=panel_alt, foreground=COLORS["text_soft"],
                    bordercolor=COLORS["neon_cyan_dim"], lightcolor=COLORS["neon_cyan_dim"],
                    darkcolor=COLORS["neon_cyan_dim"], focuscolor=panel_alt,
                    font=FONTS["button"], padding=(10, 6), relief="solid", borderwidth=1)
    style.map("TButton",
              background=[("disabled", panel_dark), ("pressed", COLORS["hover"]),
                          ("active", COLORS["hover"])],
              foreground=[("disabled", COLORS["disabled_text"]),
                          ("active", COLORS["neon_cyan"])],
              bordercolor=[("disabled", COLORS["border"]), ("active", COLORS["neon_cyan"])],
              lightcolor=[("active", COLORS["neon_cyan"])],
              darkcolor=[("active", COLORS["neon_cyan"])])

    # Главные кнопки («Нарезать», «Отмена», подтверждения): неоново-розовая
    # рамка и подпись, при наведении — яркое свечение.
    style.configure("Accent.TButton", background=panel_alt, foreground=COLORS["neon_pink"],
                    bordercolor=COLORS["neon_pink"], lightcolor=COLORS["neon_pink"],
                    darkcolor=COLORS["neon_pink"], focuscolor=COLORS["neon_pink"],
                    font=FONTS["neon_button"], padding=(16, 9), relief="solid", borderwidth=2)
    style.map("Accent.TButton",
              background=[("disabled", panel_dark), ("pressed", "#3A0A24"),
                          ("active", "#3A0A24")],
              foreground=[("disabled", COLORS["disabled_text"]),
                          ("active", COLORS["neon_pink_hot"])],
              bordercolor=[("disabled", COLORS["accent_disabled"]),
                           ("active", COLORS["neon_pink_hot"])],
              lightcolor=[("active", COLORS["neon_pink_hot"])],
              darkcolor=[("active", COLORS["neon_pink_hot"])])

    style.configure("Ghost.TButton", background=bg, foreground=COLORS["muted"],
                    bordercolor=COLORS["border"], lightcolor=bg, darkcolor=bg,
                    focuscolor=bg, font=FONTS["small"], padding=(8, 4), relief="flat",
                    borderwidth=1)
    style.map("Ghost.TButton",
              background=[("active", panel), ("disabled", bg)],
              foreground=[("disabled", COLORS["disabled_text"]), ("active", COLORS["neon_cyan"])],
              bordercolor=[("active", COLORS["neon_cyan_dim"])])

    # --- поля ввода -----------------------------------------------------------
    style.configure("TEntry", fieldbackground=panel_alt, foreground=COLORS["text"],
                    bordercolor=COLORS["neon_cyan_dim"], lightcolor=COLORS["neon_cyan_dim"],
                    darkcolor=COLORS["neon_cyan_dim"], insertcolor=COLORS["neon_cyan"],
                    selectbackground=COLORS["select"], selectforeground=COLORS["on_accent"],
                    padding=4)
    style.map("TEntry",
              fieldbackground=[("disabled", panel_dark), ("readonly", panel_dark)],
              foreground=[("disabled", COLORS["disabled_text"])],
              bordercolor=[("focus", COLORS["neon_cyan"])],
              lightcolor=[("focus", COLORS["neon_cyan"])],
              darkcolor=[("focus", COLORS["neon_cyan"])])

    # Таймкоды — моноширинным шрифтом, чтобы цифры выравнивались.
    style.configure("Time.TEntry", font=FONTS["mono"], fieldbackground=panel_alt,
                    foreground=COLORS["text_soft"], bordercolor=COLORS["neon_cyan_dim"],
                    lightcolor=COLORS["neon_cyan_dim"], darkcolor=COLORS["neon_cyan_dim"],
                    insertcolor=COLORS["neon_cyan"], selectbackground=COLORS["select"],
                    selectforeground=COLORS["on_accent"], padding=4)
    style.map("Time.TEntry",
              fieldbackground=[("disabled", panel_dark)],
              foreground=[("disabled", COLORS["disabled_text"])],
              bordercolor=[("focus", COLORS["neon_cyan"])],
              lightcolor=[("focus", COLORS["neon_cyan"])],
              darkcolor=[("focus", COLORS["neon_cyan"])])

    style.configure("TCombobox", fieldbackground=panel_alt, background=panel_alt,
                    foreground=COLORS["text"], arrowcolor=COLORS["neon_cyan"],
                    bordercolor=COLORS["neon_cyan_dim"], lightcolor=COLORS["neon_cyan_dim"],
                    darkcolor=COLORS["neon_cyan_dim"], insertcolor=COLORS["neon_cyan"],
                    selectbackground=COLORS["select"], selectforeground=COLORS["on_accent"],
                    padding=4)
    style.map("TCombobox",
              fieldbackground=[("readonly", panel_alt), ("disabled", panel_dark)],
              background=[("active", COLORS["hover"]), ("disabled", panel_dark)],
              foreground=[("disabled", COLORS["disabled_text"])],
              arrowcolor=[("disabled", COLORS["disabled_text"])])

    style.configure("TSpinbox", fieldbackground=panel_alt, background=panel_alt,
                    foreground=COLORS["text_soft"], arrowcolor=COLORS["neon_cyan"],
                    bordercolor=COLORS["neon_cyan_dim"], lightcolor=COLORS["neon_cyan_dim"],
                    darkcolor=COLORS["neon_cyan_dim"], insertcolor=COLORS["neon_cyan"],
                    selectbackground=COLORS["select"], selectforeground=COLORS["on_accent"],
                    font=FONTS["mono"], padding=4)
    style.map("TSpinbox",
              fieldbackground=[("disabled", panel_dark)],
              background=[("active", COLORS["hover"]), ("disabled", panel_dark)],
              foreground=[("disabled", COLORS["disabled_text"])],
              arrowcolor=[("disabled", COLORS["disabled_text"])])

    style.configure("TCheckbutton", background=bg, foreground=COLORS["text"],
                    focuscolor=bg, indicatorcolor=panel_alt, font=FONTS["base"], padding=2)
    style.map("TCheckbutton",
              indicatorcolor=[("selected", COLORS["neon_violet"]), ("disabled", panel_dark)],
              foreground=[("disabled", COLORS["disabled_text"])],
              background=[("active", bg)])
    style.configure("Panel.TCheckbutton", background=panel, foreground=COLORS["text"],
                    focuscolor=panel, indicatorcolor=panel_alt, font=FONTS["base"], padding=2)
    style.map("Panel.TCheckbutton",
              indicatorcolor=[("selected", COLORS["neon_violet"]), ("disabled", panel_dark)],
              foreground=[("disabled", COLORS["disabled_text"])],
              background=[("active", panel)])

    # --- таблица клипов -------------------------------------------------------
    # Моноширинный шрифт: таймкоды в колонках выравниваются по вертикали.
    style.configure("Treeview", background=panel, fieldbackground=panel,
                    foreground=COLORS["text"], bordercolor=COLORS["neon_cyan_dim"],
                    lightcolor=panel, darkcolor=panel, borderwidth=1,
                    font=FONTS["mono"], rowheight=26)
    style.map("Treeview",
              background=[("selected", COLORS["select"])],
              foreground=[("selected", COLORS["on_accent"])])
    style.configure("Treeview.Heading", background=panel_dark, foreground=COLORS["neon_cyan"],
                    font=FONTS["mono_bold"], relief="flat", padding=(6, 6))
    style.map("Treeview.Heading",
              background=[("active", COLORS["hover"])],
              foreground=[("active", COLORS["neon_cyan"])])

    # --- прогресс и прокрутка -------------------------------------------------
    style.configure("Accent.Horizontal.TProgressbar", troughcolor=panel_dark,
                    background=COLORS["neon_cyan"], bordercolor=COLORS["neon_cyan_dim"],
                    lightcolor=COLORS["neon_cyan"], darkcolor=COLORS["neon_cyan_dim"],
                    thickness=16)
    style.configure("Vertical.TScrollbar", background=panel_alt, troughcolor=bg,
                    bordercolor=COLORS["border"], arrowcolor=COLORS["neon_cyan"],
                    lightcolor=panel_alt, darkcolor=panel_alt, gripcount=0, arrowsize=14)
    style.map("Vertical.TScrollbar", background=[("active", COLORS["hover"])])
    style.configure("Horizontal.TScrollbar", background=panel_alt, troughcolor=bg,
                    bordercolor=COLORS["border"], arrowcolor=COLORS["neon_cyan"],
                    lightcolor=panel_alt, darkcolor=panel_alt, gripcount=0, arrowsize=14)
    style.map("Horizontal.TScrollbar", background=[("active", COLORS["hover"])])

    style.configure("TSeparator", background=COLORS["border"])


# --- Подсказки ---------------------------------------------------------------

class Tooltip:
    """Простая всплывающая подсказка (в т.ч. для неактивных кнопок)."""

    def __init__(self, widget: tk.Misc, text: str, delay: int = 400) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self._window = tk.Toplevel(self.widget)
            self._window.wm_overrideredirect(True)
            self._window.wm_geometry(f"+{x}+{y}")
            border = tk.Frame(self._window, background=COLORS["border"])
            border.pack()
            tk.Label(border, text=self.text, background=panel_color(), foreground=COLORS["text"],
                     font=FONTS.get("small", ("Segoe UI", 9)), justify="left",
                     wraplength=380, padx=8, pady=4).pack(padx=1, pady=1)
        except tk.TclError:
            self._window = None

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


def panel_color() -> str:
    """Цвет фона для служебных окон (подсказки, диалоги)."""
    return COLORS["panel_dark"]
