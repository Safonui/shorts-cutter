# -*- coding: utf-8 -*-
"""Графический интерфейс приложения «Нарезка Shorts» (Tkinter).

Этап 1 ТЗ: окно в тёмной теме, выбор видеофайла, показ длительности/разрешения/fps,
ввод интервалов строкой и полями «Начало»/«Конец», список клипов, проверка
интервалов. Разделы нарезки и субтитров присутствуют в окне, но пока неактивны:
они включатся на этапах 2–3.

Долгие операции (чтение параметров файла) выполняются в отдельном потоке,
результат возвращается в интерфейс через queue + after (п. 4 ТЗ).
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from appconfig import (APP_NAME, APP_PUBLISHER, APP_VERSION, config_path, load_settings,
                       save_settings)
from applog import get_logger, last_log_line, log_path, read_log_tail
from cutting import (ClipCutter, CutSettings, FfmpegNotFound, ProbeError, VideoInfo,
                     probe_video)
from dialogs import (ask_choice, ask_open_folder, ask_yes_no, show_error, show_message)
from highlights import (HighlightCancelled, HighlightError, HighlightSettings,
                       find_highlights)
from intervals import (INTERVAL_HINT, Clip, ParseError, TIMECODE_HINT,
                       clips_total_duration, format_duration, format_timecode,
                       issues_summary, parse_interval, parse_timecode,
                       split_into_chunks, validate_clips)
from subtitles import (SubtitleStyle, SubtitleTask, estimate_seconds, is_available,
                       make_work_dir, unavailable_reason)
from taskbar import TaskbarProgress
from theme import (COLORS, FONTS, Tooltip, apply_dark_titlebar, apply_theme,
                   draw_hero_backdrop, make_hud_panel, track_dark_titlebar)

log = get_logger()


def asset_path(name: str) -> Path | None:
    """Найти файл ресурса: рядом с модулями, в лаунчере или внутри .exe-сборки."""
    bases: list[Path] = [Path(__file__).resolve().parent]
    if getattr(sys, "frozen", False):
        bases.append(Path(getattr(sys, "_MEIPASS", bases[0])))
    try:
        bases.append(Path(sys.argv[0]).resolve().parent)
    except (IndexError, OSError):
        pass
    for base in bases:
        for candidate in (base / "assets" / name, base / "_app" / "assets" / name):
            if candidate.exists():
                return candidate
    return None

VIDEO_TYPES = [
    ("Видеофайлы", "*.mp4 *.mov *.mkv *.avi *.webm"),
    ("MP4", "*.mp4"),
    ("Все файлы", "*.*"),
]

FPS_POLL_MS = 150               # период опроса очереди сообщений из потоков
HERO_HEIGHT = 104               # высота градиентной шапки
LATER_TOOLTIP = "Появится на следующем этапе разработки"


class ShortsCutterApp(tk.Tk):
    """Главное окно приложения."""

    def __init__(self) -> None:
        super().__init__()

        self.title(f"{APP_NAME} — вертикальные клипы из длинного видео")
        self.geometry("1140x870")
        self.minsize(1020, 810)
        apply_theme(self)

        # --- состояние приложения -------------------------------------------
        self.video_path: Path | None = None
        self.video_info: VideoInfo | None = None
        self.clips: list[Clip] = []
        self._issues_by_index: dict[int, str] = {}
        self._messages: "queue.Queue[tuple]" = queue.Queue()
        self._probe_token = 0
        self._ticks = 0
        self._settings_window: ExportSettingsWindow | None = None
        self._cutter: ClipCutter | None = None
        self._subtitle_task: SubtitleTask | None = None
        self._analyzing = False
        self._highlight_cancel: threading.Event | None = None
        self._highlight_settings: HighlightSettings | None = None
        self._cut_failures: list[str] = []
        self._output_dir_custom = False
        self._busy = False
        self._save_job: str | None = None

        # Настройки из прошлого запуска и полоска прогресса в панели задач.
        self.settings = load_settings()
        self._output_dir_custom = bool(self.settings.get("output_dir_custom"))
        self.taskbar = TaskbarProgress(self)
        self._apply_window_icon()
        self._create_settings_vars()
        self._restore_geometry()

        self._build_layout()
        self._bind_keys()
        self._refresh_clip_list()
        self._update_action_buttons()
        self._set_status("Выберите видеофайл, чтобы начать.")
        self._update_log_line()
        self.after(FPS_POLL_MS, self._pump_messages)

    # ------------------------------------------------------- настройки и ресурсы

    def _apply_window_icon(self) -> None:
        """Иконка окна (та же, что у .exe и установщика)."""
        icon = asset_path("icon.ico")
        if icon is None:
            return
        try:
            self.iconbitmap(default=str(icon))
        except tk.TclError:
            log.info("Иконка окна не применилась, продолжаю без неё")

    def _restore_geometry(self) -> None:
        """Вернуть размер и положение окна с прошлого запуска, если они разумны."""
        saved = str(self.settings.get("geometry") or "").strip()
        if not saved or "x" not in saved:
            return
        try:
            size = saved.split("+")[0]
            width, height = (int(part) for part in size.split("x"))
        except ValueError:
            return
        if 900 <= width <= 10000 and 600 <= height <= 5000:
            self.geometry(saved)

    def _schedule_save(self) -> None:
        """Сохранять настройки не на каждое нажатие, а с небольшой задержкой."""
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = self.after(700, self._save_settings_now)

    def _settings_snapshot(self) -> dict:
        """Собрать текущие значения настроек для сохранения."""
        values = dict(self.settings)
        values.update({
            "format": self.var_format.get(),
            "crf": self.var_crf.get(),
            "output_dir": self.var_output_dir.get(),
            "output_dir_custom": bool(self._output_dir_custom),
            "chunk_len": self.var_chunk_len.get(),
            "highlight_count": self.var_highlight_count.get(),
            "subtitles": int(self.var_subtitles.get()),
            "font_size": self.var_font_size.get(),
            "sub_position": self.var_sub_position.get(),
            "sub_color": self.var_sub_color.get(),
        })
        if self.video_path is not None:
            values["last_dir"] = str(self.video_path.parent)
        try:
            values["geometry"] = self.winfo_geometry()
        except tk.TclError:
            pass
        return values

    def _save_settings_now(self) -> None:
        self._save_job = None
        self.settings = self._settings_snapshot()
        save_settings(self.settings)

    def _on_show_about(self) -> None:
        """Окно «О программе»: версия, пути, лицензия ffmpeg."""
        from cutting import ffmpeg_exe
        try:
            ffmpeg_text = ffmpeg_exe()
        except Exception as exc:            # noqa: BLE001
            ffmpeg_text = f"не найден ({exc})"
        subtitles_text = "доступны" if is_available() else "не установлены"
        show_message(
            self, f"О программе — {APP_NAME}",
            f"{APP_NAME} {APP_VERSION}\n"
            f"Издатель: {APP_PUBLISHER}\n\n"
            "Режет длинное видео на вертикальные клипы 9:16, вшивает субтитры и\n"
            "умеет находить яркие моменты по звуку.\n\n"
            f"Настройки: {config_path()}\n"
            f"Журнал: {log_path()}\n"
            f"ffmpeg: {ffmpeg_text}\n"
            f"Субтитры (faster-whisper): {subtitles_text}\n\n"
            "Внутри используется ffmpeg из пакета imageio-ffmpeg — сборка под\n"
            "лицензией GPL; исходники и текст лицензии: ffmpeg.org")
        self._set_status(f"{APP_NAME} {APP_VERSION}")

    # ------------------------------------------------------------------ вёрстка

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build_header()
        self._build_video_section()
        self._build_clips_section()
        self._build_export_section()
        self._build_action_bar()
        self._build_status_bar()

    def _build_header(self) -> None:
        """Шапка-«герой»: градиент со свечением, как на референсе."""
        hero = tk.Canvas(self, height=HERO_HEIGHT, background=COLORS["bg"],
                         highlightthickness=0, borderwidth=0, bd=0)
        hero.grid(row=0, column=0, sticky="ew")
        self.hero = hero
        hero.bind("<Configure>", self._draw_hero)
        # Страховка: если событие <Configure> по какой-то причине не пришло,
        # шапка всё равно отрисуется после того, как окно получит размер.
        self.after(50, self._draw_hero)

    def _draw_hero(self, event: tk.Event | None = None) -> None:
        hero = self.hero
        width = event.width if event is not None else hero.winfo_width()
        height = event.height if event is not None else hero.winfo_height()
        if width <= 1 or height <= 1:
            return
        hero.delete("hero_text")
        draw_hero_backdrop(hero, width, height)
        hero.create_text(26, height * 0.36, anchor="w", text="Нарезка Shorts",
                         fill=COLORS["text"], font=FONTS.get("hero", ("Segoe UI", 21, "bold")),
                         tags="hero_text")
        hero.create_text(26, height * 0.72, anchor="w",
                         text="Длинное видео → вертикальные клипы 9:16 с вшитыми субтитрами",
                         fill=COLORS["muted"], font=FONTS.get("subtitle", ("Segoe UI", 10)),
                         tags="hero_text")
        hero.create_line(0, height - 1, width, height - 1, fill=COLORS["accent_soft"],
                         tags="hero_text")

    def _make_section(self, row: int, title: str, border_color: str,
                      pady: tuple[int, int] = (10, 6), weight_row: bool = False) -> ttk.Frame:
        """Заголовок секции капсом + HUD-панель в неоновой рамке.

        Возвращает внутреннюю рамку панели — в неё секция и рисует своё содержимое.
        """
        holder = ttk.Frame(self, style="TFrame")
        holder.grid(row=row, column=0, sticky="nsew" if weight_row else "ew",
                    padx=18, pady=pady)
        holder.columnconfigure(0, weight=1)
        if weight_row:
            holder.rowconfigure(1, weight=1)

        ttk.Label(holder, text=title.upper(), style="Section.TLabel").grid(
            row=0, column=0, sticky="w", padx=(4, 0), pady=(0, 4))
        outer, inner = make_hud_panel(holder, border_color, border_width=2)
        outer.grid(row=1, column=0, sticky="nsew")
        inner.columnconfigure(0, weight=1)
        return inner

    def _build_video_section(self) -> None:
        inner = self._make_section(1, "1 · Видео", COLORS["neon_cyan"])
        inner.columnconfigure(1, weight=1)

        self.button_choose = ttk.Button(inner, text="Выбрать видео…", style="Accent.TButton",
                                        command=self._on_choose_video)
        self.button_choose.grid(row=0, column=0, sticky="w")
        Tooltip(self.button_choose, "Поддерживаются .mp4, .mov, .mkv, .avi, .webm (Ctrl+O)")

        self.var_video_name = tk.StringVar(value="файл не выбран")
        self.var_video_path = tk.StringVar(value="")
        ttk.Label(inner, textvariable=self.var_video_name, style="Value.TLabel",
                  anchor="w").grid(row=0, column=1, sticky="w", padx=(14, 0))
        ttk.Label(inner, textvariable=self.var_video_path, style="Caption.TLabel",
                  anchor="w").grid(row=1, column=1, sticky="w", padx=(14, 0), pady=(2, 0))

        # Таблица «параметр — значение» под кнопкой выбора.
        info = ttk.Frame(inner, style="Panel.TFrame")
        info.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.var_duration = tk.StringVar(value="—")
        self.var_resolution = tk.StringVar(value="—")
        self.var_fps = tk.StringVar(value="—")
        self.var_audio = tk.StringVar(value="—")

        for column, (caption, variable) in enumerate((
                ("Длительность", self.var_duration),
                ("Разрешение", self.var_resolution),
                ("Частота кадров", self.var_fps),
                ("Звуковая дорожка", self.var_audio))):
            cell = ttk.Frame(info, style="Panel.TFrame")
            cell.grid(row=0, column=column, sticky="w", padx=(0, 34))
            ttk.Label(cell, text=caption, style="Caption.TLabel").pack(anchor="w")
            ttk.Label(cell, textvariable=variable, style="Value.TLabel").pack(anchor="w")

    def _build_clips_section(self) -> None:
        inner = self._make_section(2, "2 · Клипы (интервалы)", COLORS["neon_violet"],
                                   weight_row=True)
        # Строка таблицы растягивается, но не сжимается меньше четырёх строк —
        # иначе при небольшом окне таблица клипов становится невидимой.
        inner.rowconfigure(2, weight=1, minsize=140)

        # --- строковый ввод интервала ---------------------------------------
        line_one = ttk.Frame(inner, style="Panel.TFrame")
        line_one.grid(row=0, column=0, sticky="ew")
        line_one.columnconfigure(1, weight=1)
        ttk.Label(line_one, text="Интервал:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.var_interval = tk.StringVar()
        self.entry_interval = ttk.Entry(line_one, textvariable=self.var_interval, style="Time.TEntry")
        self.entry_interval.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.button_add_interval = ttk.Button(line_one, text="Добавить интервал",
                                              command=self._on_add_interval)
        self.button_add_interval.grid(row=0, column=2)
        Tooltip(self.entry_interval, f"{INTERVAL_HINT}\nEnter — добавить в список")

        # --- поля «Начало» и «Конец» ----------------------------------------
        line_two = ttk.Frame(inner, style="Panel.TFrame")
        line_two.grid(row=1, column=0, sticky="ew", pady=(8, 10))
        ttk.Label(line_two, text="Начало:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.var_start = tk.StringVar()
        self.entry_start = ttk.Entry(line_two, textvariable=self.var_start, width=12, style="Time.TEntry")
        self.entry_start.grid(row=0, column=1, sticky="w", padx=(8, 18))
        ttk.Label(line_two, text="Конец:", style="Panel.TLabel").grid(row=0, column=2, sticky="w")
        self.var_end = tk.StringVar()
        self.entry_end = ttk.Entry(line_two, textvariable=self.var_end, width=12, style="Time.TEntry")
        self.entry_end.grid(row=0, column=3, sticky="w", padx=(8, 18))
        self.button_add_current = ttk.Button(line_two, text="Добавить текущий интервал",
                                             command=self._on_add_current_interval)
        self.button_add_current.grid(row=0, column=4, sticky="w")
        Tooltip(self.entry_start, TIMECODE_HINT)
        Tooltip(self.entry_end, TIMECODE_HINT)
        Tooltip(self.button_add_current, "Взять интервал из полей «Начало» и «Конец»")

        # --- таблица клипов --------------------------------------------------
        table = ttk.Frame(inner, style="Panel.TFrame")
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        columns = ("number", "range", "duration", "status")
        self.tree = ttk.Treeview(table, columns=columns, show="headings",
                                 selectmode="extended", height=8)
        for name, title, width, anchor in (
                ("number", "№", 60, "center"),
                ("range", "Интервал", 220, "w"),
                ("duration", "Длительность", 130, "w"),
                ("status", "Статус", 140, "w")):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, anchor=anchor, stretch=(name == "range"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.tag_configure("error", background=COLORS["error_bg"],
                                foreground=COLORS["error_text"])
        self.tree.tag_configure("ready", foreground=COLORS["ok_text"])

        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        # --- кнопки работы со списком ---------------------------------------
        controls = ttk.Frame(inner, style="Panel.TFrame")
        controls.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.button_check = ttk.Button(controls, text="Проверить интервалы",
                                       command=self._on_check_intervals)
        self.button_check.pack(side="left")
        Tooltip(self.button_check,
                "Подсветить ошибки: конец раньше начала, выход за пределы видео, пересечения")
        self.button_delete = ttk.Button(controls, text="Удалить выбранный",
                                        command=self._on_delete_selected)
        self.button_delete.pack(side="left", padx=(8, 0))
        Tooltip(self.button_delete, "Удалить выделенные клипы (клавиша Delete)")
        self.button_clear = ttk.Button(controls, text="Очистить список", style="Ghost.TButton",
                                       command=self._on_clear_clips)
        self.button_clear.pack(side="left", padx=(8, 0))

        self.var_clips_summary = tk.StringVar(value="Клипов: 0")
        ttk.Label(controls, textvariable=self.var_clips_summary,
                  style="ValueMono.TLabel").pack(side="right")

        # --- «Нарезать подряд»: равные клипы по всему видео --------------------
        chunk_line = ttk.Frame(inner, style="Panel.TFrame")
        chunk_line.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(chunk_line, text="Нарезать подряд — длина клипа, сек:",
                  style="Panel.TLabel").pack(side="left")
        self.spin_chunk = ttk.Spinbox(chunk_line, from_=5, to=3600, width=6,
                                      textvariable=self.var_chunk_len)
        self.spin_chunk.pack(side="left", padx=(8, 8))
        self.button_chunk = ttk.Button(chunk_line, text="Нарезать подряд",
                                       command=self._on_split_into_chunks)
        self.button_chunk.pack(side="left")
        Tooltip(self.button_chunk,
                "Разбить всё видео на равные клипы заданной длины и добавить их в список")
        Tooltip(self.spin_chunk, "Длина одного клипа в секундах (от 5 до 3600)")

        # --- поиск ярких моментов по звуку (расширение сверх ТЗ) ---------------
        highlight_line = ttk.Frame(inner, style="Panel.TFrame")
        highlight_line.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(highlight_line, text="Найти яркие моменты — сколько клипов:",
                  style="Panel.TLabel").pack(side="left")
        self.spin_highlight = ttk.Spinbox(highlight_line, from_=1, to=20, width=4,
                                          textvariable=self.var_highlight_count)
        self.spin_highlight.pack(side="left", padx=(8, 8))
        self.button_highlights = ttk.Button(highlight_line, text="Найти яркие моменты",
                                            command=self._on_find_highlights)
        self.button_highlights.pack(side="left")
        Tooltip(self.button_highlights,
                "По звуку найти самые «громкие» места: смех, крик, всплески и напор речи.\n"
                "Клипы той же длины, что и в «Нарезать подряд». Содержание не анализируется — "
                "это акустика.")
        Tooltip(self.spin_highlight, "Сколько ярких моментов искать (1–20)")

        ttk.Label(inner, text=f"{INTERVAL_HINT}. Пробелы допустимы, форматы можно смешивать.",
                  style="HintPanel.TLabel").grid(row=6, column=0, sticky="w", pady=(8, 0))

    def _build_export_section(self) -> None:
        inner = self._make_section(3, "3 · Параметры экспорта", COLORS["neon_cyan_dim"])
        header = ttk.Frame(inner, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        self.button_settings = ttk.Button(header, text="Настройки…",
                                          command=self._on_open_settings)
        self.button_settings.grid(row=0, column=0, sticky="w")
        Tooltip(self.button_settings, "Формат, качество и папка вывода (субтитры — этап 3)")

        self.var_export_summary = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.var_export_summary, style="Value.TLabel").grid(
            row=0, column=1, sticky="w", padx=(14, 0))
        ttk.Label(header, text="Формат, качество и папку вывода можно менять в настройках. "
                               "Субтитры — этап 3, «нарезать подряд» — этап 4.",
                  style="HintPanel.TLabel").grid(row=1, column=0, columnspan=2, sticky="w",
                                                 pady=(6, 0))
        self._update_export_summary()

    def _create_settings_vars(self) -> None:
        """Значения настроек экспорта (п. 3.3 ТЗ); берутся из прошлого запуска."""
        saved = self.settings
        self.var_format = tk.StringVar(value=str(saved.get("format")))
        self.var_face_detect = tk.IntVar(value=0)
        self.var_subtitles = tk.IntVar(value=int(saved.get("subtitles") or 0))
        self.var_font_size = tk.StringVar(value=str(saved.get("font_size")))
        self.var_sub_position = tk.StringVar(value=str(saved.get("sub_position")))
        self.var_sub_color = tk.StringVar(value=str(saved.get("sub_color")))
        self.var_crf = tk.StringVar(value=str(saved.get("crf")))
        self.var_output_dir = tk.StringVar(value=str(saved.get("output_dir") or ""))
        self.var_chunk_len = tk.StringVar(value=str(saved.get("chunk_len")))
        self.var_highlight_count = tk.StringVar(value=str(saved.get("highlight_count")))
        for variable in (self.var_format, self.var_face_detect, self.var_subtitles,
                         self.var_font_size, self.var_sub_position, self.var_sub_color,
                         self.var_crf, self.var_output_dir, self.var_chunk_len,
                         self.var_highlight_count):
            variable.trace_add("write", lambda *_args: (self._update_export_summary(),
                                                        self._schedule_save()))

    def _update_export_summary(self) -> None:
        """Короткая строка о текущих настройках — видна всегда, без открытия окна."""
        if not hasattr(self, "var_export_summary"):
            return
        if self.var_subtitles.get():
            subtitles = (f"вкл ({self.var_font_size.get()} px, {self.var_sub_position.get()}, "
                         f"{self.var_sub_color.get()})")
        else:
            subtitles = "выкл"
        self.var_export_summary.set(
            f"Формат: {self.var_format.get()} · CRF {self.var_crf.get()} · "
            f"субтитры: {subtitles} · папка: {self.var_output_dir.get()}")

    def _on_open_settings(self) -> None:
        """Открыть (или поднять уже открытое) окно настроек экспорта."""
        window = self._settings_window
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_set()
            return
        self._settings_window = ExportSettingsWindow(self)

    def _build_action_bar(self) -> None:
        """Прогресс и главные кнопки — в HUD-панели с розовой неоновой рамкой."""
        holder = ttk.Frame(self, style="TFrame")
        holder.grid(row=4, column=0, sticky="ew", padx=18, pady=(6, 6))
        holder.columnconfigure(0, weight=1)

        outer, bar = make_hud_panel(holder, COLORS["neon_pink"], border_width=2,
                                    padding=(14, 10))
        outer.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        progress_box = ttk.Frame(bar, style="Panel.TFrame")
        progress_box.grid(row=0, column=0, sticky="ew")
        progress_box.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_box, style="Accent.Horizontal.TProgressbar",
                                        mode="determinate", maximum=100, value=0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.var_progress = tk.StringVar(value="0 %")
        ttk.Label(progress_box, textvariable=self.var_progress, style="ValueMono.TLabel").grid(
            row=0, column=1, sticky="e", padx=(10, 0))

        self.button_cut = ttk.Button(bar, text="Нарезать", style="Accent.TButton",
                                     state="disabled", command=self._on_cut)
        self.button_cut.grid(row=0, column=1, padx=(14, 0))
        Tooltip(self.button_cut, "Нарезать все клипы из списка (нужны видео и клипы)")

        self.button_cancel = ttk.Button(bar, text="Отмена", style="Accent.TButton",
                                        state="disabled", command=self._on_cancel)
        self.button_cancel.grid(row=0, column=2, padx=(8, 0))
        Tooltip(self.button_cancel, "Остановить нарезку и удалить недописанный файл")

    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self, style="Bar.TFrame", padding=(14, 6))
        bar.grid(row=5, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        self.var_status = tk.StringVar(value="")
        self.status_label = ttk.Label(bar, textvariable=self.var_status, style="Status.TLabel",
                                      anchor="w")
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.var_log_line = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.var_log_line, style="Log.TLabel",
                  anchor="e").grid(row=0, column=1, sticky="e", padx=(14, 10))

        about_button = ttk.Button(bar, text="О программе", style="Ghost.TButton",
                                   command=self._on_show_about)
        about_button.grid(row=0, column=2, sticky="e", padx=(0, 8))
        Tooltip(about_button, f"{APP_NAME} {APP_VERSION}: версия, пути и лицензия ffmpeg")

        log_button = ttk.Button(bar, text="Журнал", style="Ghost.TButton",
                                command=self._on_show_log)
        log_button.grid(row=0, column=3, sticky="e")
        Tooltip(log_button, f"Последние строки файла {log_path().name}")

    def _bind_keys(self) -> None:
        self.bind("<Control-o>", lambda _e: self._on_choose_video())
        self.entry_interval.bind("<Return>", self._on_interval_enter)
        self.entry_start.bind("<Return>", lambda _e: self._on_add_current_interval())
        self.entry_end.bind("<Return>", lambda _e: self._on_add_current_interval())
        self.tree.bind("<Delete>", lambda _e: self._on_delete_selected())

    # ------------------------------------------------------------- видеофайл

    def _on_choose_video(self) -> None:
        if self.video_path is not None:
            initial = str(self.video_path.parent)
        else:
            initial = str(self.settings.get("last_dir") or "")
        selected = filedialog.askopenfilename(
            parent=self, title="Выберите видеофайл", filetypes=VIDEO_TYPES,
            initialdir=initial or None)
        if not selected:
            return

        self.video_path = Path(selected)
        self.video_info = None
        self.var_video_name.set(self.video_path.name)
        self.var_video_path.set(str(self.video_path.parent))
        self.var_duration.set("читаю…")
        self.var_resolution.set("читаю…")
        self.var_fps.set("читаю…")
        self.var_audio.set("читаю…")
        self._set_status(f"Читаю параметры файла «{self.video_path.name}»…")
        log.info("Выбран файл: %s", self.video_path)

        self._probe_token += 1
        token = self._probe_token
        threading.Thread(target=self._probe_worker, args=(self.video_path, token),
                         daemon=True).start()

    def _probe_worker(self, path: Path, token: int) -> None:
        """Поток: читает параметры файла и кладёт результат в очередь."""
        try:
            info = probe_video(path)
        except (ProbeError, FfmpegNotFound) as exc:
            log.error("Не удалось прочитать параметры %s: %s", path, exc)
            self._messages.put(("probe_error", token, str(exc)))
        except Exception as exc:  # неожиданное — тоже показываем, но пишем в журнал
            log.exception("Ошибка чтения параметров %s", path)
            self._messages.put(("probe_error", token, f"Неожиданная ошибка: {exc}"))
        else:
            self._messages.put(("probe_ok", token, info))

    def _pump_messages(self) -> None:
        """Раз в 150 мс забирает сообщения из потоков в интерфейс (п. 4 ТЗ)."""
        try:
            while True:
                kind, *payload = self._messages.get_nowait()
                self._handle_message(kind, payload)
        except queue.Empty:
            pass
        # Журнал читается с диска реже, чем опрашивается очередь.
        self._ticks += 1
        if self._ticks % 10 == 0:
            self._update_log_line()
        self.after(FPS_POLL_MS, self._pump_messages)

    def _handle_message(self, kind: str, payload: list) -> None:
        if kind == "probe_ok":
            token, info = payload
            if token != self._probe_token:
                return
            self._apply_video_info(info)
        elif kind == "probe_error":
            token, message = payload
            if token != self._probe_token:
                return
            self.var_duration.set("—")
            self.var_resolution.set("—")
            self.var_fps.set("—")
            self.var_audio.set("—")
            self._set_status("Не удалось прочитать параметры файла.", error=True)
            show_error(self, "Не удалось открыть видео", message)
        elif kind == "status":
            self._set_status(str(payload[0]))
        elif kind == "cut":
            event_kind, event_payload = payload[0], payload[1]
            self._handle_cut_event(str(event_kind), dict(event_payload))
        elif kind == "highlights":
            event_kind, event_payload = payload[0], payload[1]
            self._handle_highlight_event(str(event_kind), dict(event_payload))

    def _apply_video_info(self, info: VideoInfo) -> None:
        self.video_info = info
        if not self._output_dir_custom:
            self.var_output_dir.set(str(info.path.parent / "Shorts"))
        self.var_duration.set(format_timecode(info.duration))
        self.var_resolution.set(info.resolution_text)
        self.var_fps.set(info.fps_text)
        self.var_audio.set(info.audio_text)
        log.info("Параметры файла: длительность %.2f с, %s, %s fps, звук: %s",
                 info.duration, info.resolution_text, info.fps_text, info.audio_text)
        self._validate(mark_rows=True)
        self._update_action_buttons()
        self._set_status(
            f"Видео «{info.path.name}»: {format_timecode(info.duration)}, "
            f"{info.resolution_text}, {info.fps_text} fps.")

    # ---------------------------------------------------------- список клипов

    def _on_interval_enter(self, _event: tk.Event) -> str:
        self._on_add_interval()
        return "break"

    def _on_add_interval(self) -> None:
        text = self.var_interval.get().strip()
        if not text:
            show_error(self, "Пустой интервал",
                       f"Введите интервал в поле, например 01:23-02:45.\n\n{INTERVAL_HINT}")
            self.entry_interval.focus_set()
            return
        try:
            start, end = parse_interval(text)
        except ParseError as exc:
            show_error(self, "Не удалось разобрать интервал", f"{exc}\n\n{INTERVAL_HINT}")
            self.entry_interval.focus_set()
            self.entry_interval.select_range(0, "end")
            return
        self._append_clip(start, end)
        self.var_interval.set("")

    def _on_add_current_interval(self) -> None:
        raw_start = self.var_start.get().strip()
        raw_end = self.var_end.get().strip()
        if not raw_start or not raw_end:
            show_error(self, "Не хватает границ",
                       f"Заполните оба поля — «Начало» и «Конец».\n\n{TIMECODE_HINT}")
            return
        try:
            start = parse_timecode(raw_start)
            end = parse_timecode(raw_end)
        except ParseError as exc:
            show_error(self, "Не удалось разобрать время", f"{exc}\n\n{TIMECODE_HINT}")
            return
        self._append_clip(start, end)
        self.var_start.set("")
        self.var_end.set("")

    def _append_clip(self, start: float, end: float) -> None:
        clip = Clip(start=start, end=end)
        self.clips.append(clip)
        index = len(self.clips) - 1
        self._validate(mark_rows=True)
        self._refresh_clip_list(select_index=index)
        self._update_action_buttons()
        note = "" if clip.is_valid else " Проверьте границы: конец не позже начала."
        self._set_status(
            f"Добавлен клип №{index + 1}: {clip.label()} ({format_duration(clip.duration)}).{note}",
            error=not clip.is_valid)
        log.info("Добавлен клип №%d: %.2f–%.2f с", index + 1, start, end)

    def _on_delete_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            show_message(self, "Ничего не выбрано",
                         "Выберите одну или несколько строк в списке клипов.")
            return
        for position in sorted((int(item) for item in selected), reverse=True):
            if 0 <= position < len(self.clips):
                removed = self.clips.pop(position)
                log.info("Удалён клип: %.2f–%.2f с", removed.start, removed.end)
        self._validate(mark_rows=True)
        self._refresh_clip_list()
        self._update_action_buttons()
        self._set_status(f"Удалено клипов: {len(selected)}.")

    def _on_clear_clips(self) -> None:
        if not self.clips:
            self._set_status("Список клипов уже пуст.")
            return
        if not ask_yes_no(self, "Очистить список",
                          f"Удалить все клипы из списка ({len(self.clips)} шт.)?"):
            return
        self.clips.clear()
        self._validate(mark_rows=True)
        self._refresh_clip_list()
        self._update_action_buttons()
        self._set_status("Список клипов очищен.")
        log.info("Список клипов очищен пользователем")

    def _refresh_clip_list(self, select_index: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, clip in enumerate(self.clips):
            tags: list[str] = []
            severity = self._issues_by_index.get(index)
            if severity == "error":
                tags.append("error")
            elif clip.status == "готов":
                tags.append("ready")
            self.tree.insert("", "end", iid=str(index), tags=tuple(tags), values=(
                index + 1,
                clip.label(),
                format_duration(clip.duration),
                clip.status,
            ))
        total = clips_total_duration(self.clips)
        summary = f"Клипов: {len(self.clips)}"
        if self.clips:
            summary += f" · суммарно {format_duration(total)}"
        self.var_clips_summary.set(summary)
        if select_index is not None and 0 <= select_index < len(self.clips):
            item = str(select_index)
            self.tree.selection_set(item)
            self.tree.focus(item)
            self.tree.see(item)

    def _on_split_into_chunks(self) -> None:
        """«Нарезать подряд»: разбить всё видео на равные клипы (этап 4)."""
        if self.video_info is None:
            show_message(self, "Видео не выбрано",
                         "Сначала выберите видеофайл, тогда его можно будет разбить "
                         "на равные клипы.")
            return
        raw = self.var_chunk_len.get().strip().replace(",", ".")
        try:
            chunk = float(raw)
        except ValueError:
            show_error(self, "Неверная длина клипа",
                       f"«{raw}» — это не число. Укажите длину клипа в секундах, "
                       "например 60.")
            return
        if chunk < 1:
            show_error(self, "Слишком короткий клип",
                       "Длина клипа должна быть не меньше секунды.")
            return

        clips = split_into_chunks(self.video_info.duration, chunk)
        if not clips:
            show_message(self, "Клипы не получились",
                         f"Длина клипа ({format_duration(chunk)}) больше самого видео "
                         f"({format_timecode(self.video_info.duration)}).")
            return

        total = sum(item.duration for item in clips)
        if len(clips) > 50 and not ask_yes_no(
                self, "Получится много клипов",
                f"Видео разобьётся на {len(clips)} клипов по {format_duration(chunk)}.\n"
                f"Нарезка займёт заметное время.\n\nПродолжить?",
                yes_label="Продолжить"):
            return

        action = "replace"
        if self.clips:
            choice = ask_choice(
                self, "Список клипов уже не пуст",
                f"Будет подготовлено клипов: {len(clips)} по {format_duration(chunk)} "
                f"(всего {format_duration(total)}).\n\nЧто сделать с текущим списком?",
                (("Заменить список", "replace"), ("Добавить в конец", "append")))
            # Любой неожиданный ответ (в том числе закрытие окна) — это отмена:
            # менять список пользователя без явного согласия нельзя.
            if choice not in ("replace", "append"):
                self._set_status("«Нарезать подряд» отменено: список оставлен как был.")
                return
            action = str(choice)

        if action == "replace":
            self.clips = clips
        else:
            self.clips.extend(clips)

        self._validate(mark_rows=True)
        self._refresh_clip_list(select_index=0)
        self._update_action_buttons()
        self._set_status(
            f"«Нарезать подряд»: добавлено клипов {len(clips)} по {format_duration(chunk)}; "
            f"всего в списке {len(self.clips)}.")
        log.info("«Нарезать подряд»: %d клипов по %.1f с (%s)",
                 len(clips), chunk, action)

    def _on_find_highlights(self) -> None:
        """Найти яркие моменты по звуку (расширение сверх ТЗ)."""
        if self.video_info is None:
            show_message(self, "Видео не выбрано",
                         "Сначала выберите видеофайл — приложение проанализирует его звук.")
            return
        if not self.video_info.has_audio:
            show_error(self, "В видео нет звука",
                       "Искать яркие моменты не по чему: в этом файле нет звуковой "
                       "дорожки.")
            return
        if self._cutter is not None or self._analyzing:
            return

        try:
            count = int(float(self.var_highlight_count.get().strip().replace(",", ".")))
        except ValueError:
            show_error(self, "Неверное число клипов",
                       f"«{self.var_highlight_count.get()}» — это не число. Укажите, "
                       "сколько ярких моментов искать (1–20).")
            return
        try:
            length = float(self.var_chunk_len.get().strip().replace(",", "."))
        except ValueError:
            length = 30.0
        settings = HighlightSettings(clip_length=max(2.0, length),
                                     max_clips=max(1, min(20, count)))

        self._highlight_settings = settings
        self._highlight_cancel = threading.Event()
        self._analyzing = True
        self.progress.configure(value=0)
        self.var_progress.set("0 %")
        self.taskbar.set_progress(0)
        self._set_busy(True)
        self._set_status(f"Анализирую звук: ищу {settings.max_clips} ярких моментов "
                         f"по {format_duration(settings.clip_length)}…")
        log.info("Поиск ярких моментов: клипов %d по %.0f с",
                 settings.max_clips, settings.clip_length)
        threading.Thread(target=self._highlight_worker, args=(self.video_info, settings),
                         daemon=True).start()

    def _highlight_worker(self, info: VideoInfo, settings: HighlightSettings) -> None:
        """Тяжёлый анализ звука — в отдельном потоке, события через очередь."""
        post = lambda kind, payload=None: self._messages.put(  # noqa: E731
            ("highlights", kind, payload or {}))
        try:
            moments = find_highlights(
                info, settings,
                on_progress=lambda percent: post("progress", {"percent": percent}),
                should_cancel=self._highlight_cancel.is_set,
                on_stage=lambda stage: post("stage", {"stage": stage}))
        except HighlightCancelled:
            post("cancelled")
        except HighlightError as exc:
            log.error("Поиск ярких моментов не удался: %s", exc)
            post("error", {"message": str(exc)})
        except Exception as exc:                    # noqa: BLE001 — не роняем поток
            log.exception("Неожиданная ошибка поиска ярких моментов")
            post("error", {"message": f"Неожиданная ошибка: {exc}"})
        else:
            post("done", {"moments": moments})

    def _handle_highlight_event(self, kind: str, payload: dict) -> None:
        if kind == "stage":
            stage = str(payload.get("stage", ""))
            text = {"audio": "Читаю звуковую дорожку…",
                    "scoring": "Считаю «яркость» участков…"}.get(stage, "Анализирую…")
            self._set_status(text)
        elif kind == "progress":
            percent = float(payload.get("percent", 0.0))
            self.progress.configure(value=percent)
            self.var_progress.set(f"{percent:.0f} %")
            self.taskbar.set_progress(percent)
        elif kind == "cancelled":
            self._finish_highlights()
            self._set_status("Поиск ярких моментов отменён.")
        elif kind == "error":
            self._finish_highlights()
            self._set_status("Не удалось проанализировать звук.", error=True)
            show_error(self, "Не удалось найти яркие моменты", str(payload.get("message", "")))
        elif kind == "done":
            self._finish_highlights()
            self._apply_highlights(list(payload.get("moments") or []))

    def _finish_highlights(self) -> None:
        self._analyzing = False
        self._highlight_cancel = None
        self.progress.configure(value=0)
        self.var_progress.set("0 %")
        self.taskbar.clear()
        self._set_busy(False)

    def _apply_highlights(self, moments: list) -> None:
        """Показать найденные моменты и добавить их в список клипов."""
        if not moments:
            self._set_status("Ярких моментов не нашлось — попробуйте другую длину клипа.")
            show_message(self, "Яркие моменты не найдены",
                         "Не нашлось участков, заметно выделяющихся по звуку.\n\n"
                         "Попробуйте увеличить длину клипа или выбрать видео, где есть "
                         "смех, крики, аплодисменты или резкие всплески.")
            return

        clips = [moment.clip for moment in moments]
        total = sum(item.duration for item in clips)
        action = "replace"
        if self.clips:
            choice = ask_choice(
                self, "Список клипов уже не пуст",
                f"Найдено моментов: {len(clips)} по {format_duration(clips[0].duration)} "
                f"(всего {format_duration(total)}).\n\nЧто сделать с текущим списком?",
                (("Заменить список", "replace"), ("Добавить в конец", "append")))
            if choice not in ("replace", "append"):
                self._set_status("Найденные моменты не добавлены: список оставлен как был.")
                return
            action = str(choice)

        if action == "replace":
            self.clips = clips
        else:
            self.clips.extend(clips)
        self._validate(mark_rows=True)
        self._refresh_clip_list(select_index=0)
        self._update_action_buttons()
        self._set_status(f"Найдено ярких моментов: {len(clips)}; всего в списке "
                         f"{len(self.clips)}.")

        lines = [f"Найдено моментов: {len(clips)}", ""]
        for index, moment in enumerate(sorted(moments, key=lambda item: item.clip.start), 1):
            lines.append(f"{index}. {format_timecode(moment.clip.start)}–"
                         f"{format_timecode(moment.clip.end)} — {moment.describe()}")
        lines += ["", "Это акустика: громкость, всплески и высокие частоты "
                      "(смех, крик, аплодисменты). Смысл речи не анализируется.",
                  "Клипы добавлены в список — можно проверить и нарезать."]
        log.info("Яркие моменты добавлены в список: %d (%s)", len(clips), action)
        show_message(self, "Яркие моменты", "\n".join(lines))

    def _on_check_intervals(self) -> None:
        if not self.clips:
            show_message(self, "Список пуст",
                         "Добавьте хотя бы один интервал, например 01:23-02:45.")
            return
        issues = self._validate(mark_rows=True)
        errors = [issue for issue in issues if issue.severity == "error"]
        text = issues_summary(issues)
        if errors:
            show_error(self, "Найдены ошибки в интервалах", text)
            self._set_status(f"Ошибок в интервалах: {len(errors)}.", error=True)
        else:
            show_message(self, "Проверка интервалов", text)
            self._set_status("Интервалы корректны.")

    def _validate(self, mark_rows: bool = True) -> list:
        """Проверить интервалы и (при mark_rows) запомнить подсветку строк."""
        duration = self.video_info.duration if self.video_info else None
        issues = validate_clips(self.clips, duration)
        self._issues_by_index = {}
        for issue in issues:
            if issue.severity == "error" and issue.index >= 0:
                self._issues_by_index[issue.index] = "error"
        if mark_rows:
            self._refresh_clip_list()
        return issues

    # ---------------------------------------------------------------- прочее

    # ---------------------------------------------------------------- нарезка

    def _subtitles_enabled(self) -> bool:
        """Включены ли субтитры в настройках."""
        return bool(self.var_subtitles.get())

    def _subtitle_style(self) -> SubtitleStyle:
        """Оформление субтитров из настроек (п. 3.3 ТЗ)."""
        try:
            font_size = int(float(self.var_font_size.get().strip().replace(",", ".")))
        except ValueError:
            font_size = 48
        return SubtitleStyle(font_size=max(12, min(200, font_size)),
                             position=self.var_sub_position.get().strip() or "низ",
                             color=self.var_sub_color.get().strip() or "белый")

    def _update_subtitle_controls(self) -> None:
        """Поля оформления субтитров активны только при включённых субтитрах."""
        window = self._settings_window
        if window is not None and window.winfo_exists():
            window.apply_subtitle_state()
        else:
            self._update_export_summary()

    def _collect_settings(self) -> CutSettings:
        """Собрать настройки экспорта из полей окна настроек."""
        vertical = self.var_format.get().strip().startswith("Вертикаль")
        try:
            crf = int(float(self.var_crf.get().strip().replace(",", ".")))
        except ValueError:
            crf = 20
        settings = CutSettings(vertical=vertical, crf=crf)
        settings.crf = settings.clamped_crf()
        return settings

    def _output_directory(self) -> Path:
        """Папка вывода: своя или подпапка «Shorts» рядом с исходным видео."""
        raw = self.var_output_dir.get().strip()
        if self._output_dir_custom and raw:
            return Path(raw)
        if self.video_path is not None:
            return self.video_path.parent / "Shorts"
        return Path.cwd() / "Shorts"

    def _on_browse_output(self) -> None:
        initial = self.var_output_dir.get().strip()
        if not initial or not Path(initial).is_dir():
            initial = str(self.video_path.parent) if self.video_path else ""
        chosen = filedialog.askdirectory(parent=self._settings_window or self,
                                         title="Папка для готовых клипов",
                                         initialdir=initial or None)
        if chosen:
            self._output_dir_custom = True
            self.var_output_dir.set(chosen)
            self._set_status(f"Папка вывода: {chosen}")

    def _on_cut(self) -> None:
        """Запустить нарезку всех клипов в отдельном потоке."""
        if self._cutter is not None:
            return
        if self.video_info is None:
            show_message(self, "Видео не выбрано",
                         "Сначала нажмите «Выбрать видео…» и дождитесь чтения параметров.")
            return
        if not self.clips:
            show_message(self, "Список клипов пуст",
                         "Добавьте хотя бы один интервал, например 0:30-0:45.")
            return

        issues = self._validate(mark_rows=True)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            show_error(self, "Нарезка не запущена: ошибки в интервалах",
                       issues_summary(issues) + "\n\nИсправьте интервалы или удалите "
                                               "ошибочные строки и повторите.")
            self._set_status(f"Нарезка не запущена: ошибок {len(errors)}.", error=True)
            return

        output_dir = self._output_directory()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            show_error(self, "Не удалось создать папку вывода",
                       f"{output_dir}\n\n{exc}\n\nВыберите другую папку в настройках.")
            return

        settings = self._collect_settings()
        subtitle_task = self._prepare_subtitle_task(output_dir)
        if self._subtitles_enabled() and subtitle_task is None:
            return                       # пользователь отказался или модуль недоступен

        for clip in self.clips:
            clip.status = "—"
        self._cut_failures = []
        self._refresh_clip_list()
        self.progress.configure(value=0)
        self.var_progress.set("0 %")
        self._set_busy(True)
        self.taskbar.set_progress(0)
        self._set_status(f"Нарезка: клипов {len(self.clips)} → {output_dir}")
        log.info("Нарезка запущена: %d клипов, папка %s, вертикаль=%s, CRF=%d, субтитры=%s",
                 len(self.clips), output_dir, settings.vertical, settings.crf,
                 self._subtitles_enabled())

        self._cutter = ClipCutter(self.video_info, list(self.clips), output_dir, settings,
                                  on_event=self._on_cut_event,
                                  subtitles_for=(subtitle_task.filter_for
                                                 if subtitle_task else None))
        threading.Thread(target=self._cutter.run, daemon=True).start()

    def _prepare_subtitle_task(self, output_dir: Path) -> SubtitleTask | None:
        """Проверить доступность субтитров, предупредить о времени и создать задачу."""
        if not self._subtitles_enabled():
            self._subtitle_task = None
            return None
        if not is_available():
            show_error(self, "Субтитры недоступны",
                       unavailable_reason() + "\n\nСнимите галочку «Вшить субтитры» "
                                             "в настройках и повторите нарезку.")
            return None

        total = sum(clip.duration for clip in self.clips)
        low, high = estimate_seconds(total)
        agreed = ask_yes_no(
            self, "Распознавание речи",
            f"Клипов: {len(self.clips)}, суммарно {format_duration(total)}.\n\n"
            f"Модель small распознаёт речь примерно за {format_duration(low)} – "
            f"{format_duration(high)} (на CPU это 1–3× длительности клипов).\n"
            "При первом запуске модель (~500 МБ) скачается из интернета, дальше "
            "работает офлайн.\n\nПродолжить нарезку с субтитрами?",
            yes_label="Продолжить")
        if not agreed:
            self._set_status("Нарезка отменена: пользователь отказался от субтитров.")
            self._subtitle_task = None
            return None

        self._subtitle_task = SubtitleTask(self.video_info, make_work_dir(output_dir),
                                           self._subtitle_style(),
                                           on_event=self._on_cut_event)
        return self._subtitle_task

    def _on_cut_event(self, kind: str, payload: dict) -> None:
        """Событие из потока нарезки — только в очередь, без обращения к Tk."""
        self._messages.put(("cut", kind, payload))

    def _on_cancel(self) -> None:
        """Кнопка «Отмена»: остановить анализ звука или убить текущий ffmpeg."""
        if self._analyzing and self._highlight_cancel is not None:
            self.button_cancel.state(["disabled"])
            self._set_status("Останавливаю анализ звука…")
            log.info("Пользователь отменил поиск ярких моментов")
            self._highlight_cancel.set()
            return
        if self._cutter is None:
            return
        self.button_cancel.state(["disabled"])
        self._set_status("Останавливаю нарезку…")
        log.info("Пользователь нажал «Отмена»")
        self._cutter.cancel()

    def _handle_cut_event(self, kind: str, payload: dict) -> None:
        if kind == "started":
            self._set_status(f"Нарезка началась: клипов {payload.get('total', 0)}.")
        elif kind == "clip_start":
            index = int(payload.get("index", 0))
            total = int(payload.get("total", 0))
            self.progress.configure(value=0)
            self.var_progress.set("0 %")
            self._set_clip_status(index, "в работе")
            self._set_status(f"Клип {index + 1} из {total}: {payload.get('name', '')}")
        elif kind == "clip_progress":
            percent = float(payload.get("percent", 0.0))
            self.progress.configure(value=percent)
            self.var_progress.set(f"{percent:.0f} %")
            self.taskbar.set_progress(percent)
        elif kind == "clip_done":
            index = int(payload.get("index", 0))
            self._set_clip_status(index, "готов")
            self.progress.configure(value=100)
            self.var_progress.set("100 %")
            self._set_status(f"Готов клип: {payload.get('name', '')} "
                             f"({format_duration(float(payload.get('duration', 0.0)))})")
        elif kind == "clip_error":
            index = int(payload.get("index", 0))
            message = str(payload.get("message", ""))
            self._cut_failures.append(f"Клип №{index + 1}: {message}")
            self._set_clip_status(index, "ошибка")
            self._set_status(f"Ошибка на клипе №{index + 1}.", error=True)
        elif kind == "cancelled":
            index = int(payload.get("index", 0))
            self._set_clip_status(index, "отменён")
            self._set_status("Нарезка отменена пользователем.")
        elif kind == "subtitle_start":
            index = int(payload.get("index", 0))
            self._set_clip_status(index, "речь")
            self._set_status(f"Клип №{index + 1}: готовлю субтитры…")
        elif kind == "subtitle_stage":
            index = int(payload.get("index", 0))
            stage = str(payload.get("stage", ""))
            stages = {"audio": "вырезаю звук", "model": "загружаю модель распознавания",
                      "speech": "распознаю речь"}
            self._set_status(f"Клип №{index + 1}: {stages.get(stage, 'субтитры')}…")
        elif kind == "subtitle_progress":
            percent = float(payload.get("percent", 0.0))
            self.progress.configure(value=percent)
            self.var_progress.set(f"{percent:.0f} %")
        elif kind == "subtitle_ready":
            index = int(payload.get("index", 0))
            self._set_status(f"Клип №{index + 1}: распознано сегментов "
                             f"{payload.get('segments', 0)}, вшиваю субтитры…")
        elif kind == "subtitle_empty":
            index = int(payload.get("index", 0))
            self._set_status(f"Клип №{index + 1}: речь не распознана — клип без субтитров.")
        elif kind == "finished":
            self._on_cut_finished(payload)

    def _set_clip_status(self, index: int, status: str) -> None:
        if 0 <= index < len(self.clips):
            self.clips[index].status = status
            self._refresh_clip_list(select_index=index)

    def _on_cut_finished(self, payload: dict) -> None:
        """Нарезка завершена: показать список файлов и предложить открыть папку."""
        self._cutter = None
        self._set_busy(False)
        task = self._subtitle_task
        self._subtitle_task = None
        if task is not None:
            task.cleanup()               # временные .wav и .srt больше не нужны
        results = payload.get("results") or []
        ready = [result for result in results if result.ok]
        cancelled = bool(payload.get("cancelled"))

        if not ready and not cancelled:
            text = "\n".join(self._cut_failures) or "Ни один клип не был создан."
            show_error(self, "Не удалось нарезать клипы", text)
            self._set_status("Нарезка не удалась.", error=True)
            return

        folder = self._output_directory()
        lines = [f"Готово клипов: {len(ready)} из {len(self.clips)}", ""]
        total_seconds = 0.0
        for result in ready:
            total_seconds += result.duration
            lines.append(f"• {result.path.name} — {format_duration(result.duration)}")
        if self._cut_failures:
            lines += ["", "Не удалось:"] + [f"• {item}" for item in self._cut_failures]
        lines += ["", f"Папка: {folder}"]
        if cancelled:
            lines.insert(0, "Нарезка остановлена пользователем.")
            lines.insert(1, "")

        if cancelled:
            self._set_status(f"Нарезка отменена. Готовых клипов: {len(ready)}.")
        else:
            self._set_status(f"Готово клипов: {len(ready)} из {len(self.clips)} "
                             f"({format_duration(total_seconds)}).")
        log.info("Нарезка завершена: готово %d из %d, отменена: %s",
                 len(ready), len(self.clips), cancelled)
        if ask_open_folder(self, "Готовые клипы", "\n".join(lines)):
            self._open_folder(folder)

    def _open_folder(self, folder: Path) -> None:
        """Открыть папку в проводнике (п. 3.4 ТЗ, os.startfile)."""
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]  # только Windows
        except (AttributeError, OSError) as exc:
            log.error("Не удалось открыть папку %s: %s", folder, exc)
            show_error(self, "Не удалось открыть папку",
                       f"{folder}\n\n{exc}\n\nОткройте её вручную в проводнике.")

    def _set_busy(self, busy: bool) -> None:
        """Во время нарезки менять можно только «Отмена»."""
        for widget in (self.button_cut, self.button_choose, self.button_add_interval,
                       self.button_add_current, self.button_check, self.button_delete,
                       self.button_clear, self.button_settings, self.button_chunk,
                       self.button_highlights, self.spin_chunk, self.spin_highlight):
            if busy:
                widget.state(["disabled"])
            else:
                widget.state(["!disabled"])
        if busy:
            self.button_cancel.state(["!disabled"])
        else:
            self.button_cancel.state(["disabled"])
        self._busy = busy
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        """«Нарезать» активна, когда есть видео и хотя бы один клип."""
        if getattr(self, "_busy", False):
            return
        ready = self.video_info is not None and bool(self.clips)
        # Ошибочные интервалы не мешают нажать кнопку: причина будет показана.
        self.button_cut.state(["!disabled"] if ready else ["disabled"])

    def _on_show_log(self) -> None:
        path = log_path()
        tail = read_log_tail(25)
        if not tail:
            show_error(self, "Журнал недоступен",
                       f"Не удалось прочитать файл журнала:\n{path}")
            return
        show_message(self, "Журнал работы", f"Файл: {path}\n\n" + "\n".join(tail))

    def _update_log_line(self) -> None:
        self.var_log_line.set(last_log_line(default=""))

    def _set_status(self, text: str, error: bool = False) -> None:
        self.var_status.set(text)
        try:
            self.status_label.configure(
                foreground=COLORS["error_text"] if error else COLORS["muted"])
        except tk.TclError:
            pass

    def report_callback_exception(self, exc, value, tb) -> None:  # noqa: N802 (Tk API)
        """Ошибки интерфейса — в журнал и понятным окном, без traceback на экране."""
        log.error("Необработанная ошибка интерфейса", exc_info=(exc, value, tb))
        try:
            show_error(self, "Неожиданная ошибка",
                       f"Произошла ошибка:\n{value}\n\nПодробности записаны в "
                       f"{log_path().name}.")
        except Exception:
            pass

    def destroy(self) -> None:
        """Перед закрытием сохранить настройки и убрать полоску прогресса."""
        try:
            if self._save_job is not None:
                self.after_cancel(self._save_job)
                self._save_job = None
            self._save_settings_now()
            self.taskbar.clear()
        except Exception:                   # noqa: BLE001 — закрытие не должно падать
            log.exception("Не удалось сохранить настройки при закрытии")
        log.info("Окно закрыто пользователем")
        super().destroy()


class ExportSettingsWindow(tk.Toplevel):
    """Окно настроек экспорта (п. 3.3 ТЗ).

    На этапе 1 все элементы неактивны — они включатся на этапах 2–4, но места
    в настройках уже заняты и значения по умолчанию соответствуют ТЗ.
    """

    def __init__(self, app: ShortsCutterApp) -> None:
        super().__init__(app)
        self.app = app
        self.title("Настройки экспорта")
        self.configure(background=COLORS["bg"])
        self.resizable(False, False)
        self.transient(app)

        outer, body = make_hud_panel(self, COLORS["neon_cyan"], border_width=2,
                                     padding=(18, 16))
        outer.pack(fill="both", expand=True, padx=12, pady=12)
        for column in range(3):
            body.columnconfigure(column, weight=1 if column == 1 else 0)

        ttk.Label(body, text="ПАРАМЕТРЫ ЭКСПОРТА", style="Panel.TLabel",
                  font=FONTS.get("section", ("Segoe UI", 11, "bold")),
                  foreground=COLORS["neon_cyan"]).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="Активны: формат, качество и папка вывода. Пока неактивны: "
                             "субтитры — этап 3, «нарезать подряд» — этап 4.",
                  style="Caption.TLabel").grid(row=1, column=0, columnspan=3, sticky="w",
                                               pady=(4, 14))

        # --- формат вывода ----------------------------------------------------
        ttk.Label(body, text="Формат вывода:", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w", pady=4)
        combo_format = ttk.Combobox(body, textvariable=app.var_format, state="readonly",
                                    values=("Вертикаль 9:16 (1080×1920)", "Оставить как есть"),
                                    width=34)
        combo_format.grid(row=2, column=1, columnspan=2, sticky="w", pady=4)
        Tooltip(combo_format, "Вертикаль: центральный кроп до 9:16, затем масштаб 1080×1920")

        check_face = ttk.Checkbutton(body, text="Автодетект лица (позже)",
                                     variable=app.var_face_detect, state="disabled",
                                     style="Panel.TCheckbutton")
        check_face.grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        Tooltip(check_face, "В первой версии не реализуется — место в настройках оставлено")

        # --- субтитры ---------------------------------------------------------
        # Если пакет faster-whisper не установлен, галочка остаётся серой и рядом
        # появляется понятное объяснение (п. 2 ТЗ, критерий приёмки № 7).
        subs_available = is_available()
        check_subs = ttk.Checkbutton(body, text="Вшить субтитры", variable=app.var_subtitles,
                                     command=app._update_subtitle_controls,
                                     style="Panel.TCheckbutton",
                                     state="normal" if subs_available else "disabled")
        check_subs.grid(row=4, column=0, sticky="w", pady=(12, 4))
        Tooltip(check_subs, "Распознавание речи через faster-whisper (модель small)"
                            if subs_available else unavailable_reason())

        self.subs_hint = ttk.Label(body, text="", style="Caption.TLabel", wraplength=520,
                                   justify="left")
        self.subs_hint.grid(row=4, column=1, columnspan=2, sticky="w", pady=(12, 4))
        if not subs_available:
            self.subs_hint.configure(
                text="Недоступно: не установлен faster-whisper — "
                     "нарезка работает без субтитров (pip install faster-whisper)")

        subs_line = ttk.Frame(body, style="Panel.TFrame")
        subs_line.grid(row=5, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Label(subs_line, text="Размер шрифта:", style="Caption.TLabel").pack(side="left")
        self.subs_controls = []
        self.subs_controls.append(
            ttk.Spinbox(subs_line, from_=24, to=96, textvariable=app.var_font_size, width=5))
        self.subs_controls[-1].pack(side="left", padx=(6, 16))
        ttk.Label(subs_line, text="Положение:", style="Caption.TLabel").pack(side="left")
        self.subs_controls.append(
            ttk.Combobox(subs_line, textvariable=app.var_sub_position, width=8,
                         values=("низ", "центр")))
        self.subs_controls[-1].pack(side="left", padx=(6, 16))
        ttk.Label(subs_line, text="Цвет:", style="Caption.TLabel").pack(side="left")
        self.subs_controls.append(
            ttk.Combobox(subs_line, textvariable=app.var_sub_color, width=8,
                         values=("белый", "жёлтый")))
        self.subs_controls[-1].pack(side="left", padx=(6, 16))
        ttk.Label(subs_line, text="обводка чёрная всегда", style="Caption.TLabel").pack(side="left")
        self.apply_subtitle_state()

        # --- качество ---------------------------------------------------------
        ttk.Label(body, text="Качество (CRF):", style="Panel.TLabel").grid(
            row=6, column=0, sticky="w", pady=(12, 4))
        spin_crf = ttk.Spinbox(body, from_=16, to=28, textvariable=app.var_crf, width=6)
        spin_crf.grid(row=6, column=1, columnspan=2, sticky="w", pady=(12, 4))
        ttk.Label(body, text="меньше — лучше качество и больше файл", style="Caption.TLabel").grid(
            row=7, column=0, columnspan=3, sticky="w")
        Tooltip(spin_crf, "Диапазон 16–28, по умолчанию 20")

        # --- папка вывода -----------------------------------------------------
        ttk.Label(body, text="Папка вывода:", style="Panel.TLabel").grid(
            row=8, column=0, sticky="w", pady=(12, 4))
        entry_out = ttk.Entry(body, textvariable=app.var_output_dir, width=30)
        entry_out.grid(row=8, column=1, sticky="ew", pady=(12, 4))
        browse_out = ttk.Button(body, text="Обзор…", command=app._on_browse_output)
        browse_out.grid(row=8, column=2, sticky="w", padx=(10, 0), pady=(12, 4))
        Tooltip(browse_out, "Своя папка для готовых клипов")

        # «Нарезать подряд» переехало в главное окно, к списку клипов:
        # там же видно, что получилось, и можно сразу нарезать.
        ttk.Label(body, text="Разбить видео на равные клипы можно в главном окне — "
                             "кнопка «Нарезать подряд» под списком клипов.",
                  style="Caption.TLabel", wraplength=520,
                  justify="left").grid(row=9, column=0, columnspan=3, sticky="w",
                                       pady=(14, 4))

        ttk.Button(body, text="Закрыть", style="Accent.TButton",
                   command=self.destroy).grid(row=10, column=2, sticky="e", pady=(18, 0))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._center(app)
        apply_dark_titlebar(self)
        track_dark_titlebar(self)

    def apply_subtitle_state(self) -> None:
        """Включить поля оформления субтитров, если они доступны и включены."""
        available = is_available()
        enabled = available and bool(self.app.var_subtitles.get())
        for widget in getattr(self, "subs_controls", []):
            try:
                widget.state(["!disabled"] if enabled else ["disabled"])
            except tk.TclError:
                pass
        hint = getattr(self, "subs_hint", None)
        if hint is None:
            return
        if not available:
            hint.configure(text="Недоступно: не установлен faster-whisper — "
                                "нарезка работает без субтитров (pip install faster-whisper)")
        elif enabled:
            hint.configure(text="Речь распознаёт модель small: примерно 1–3× длительности "
                                "клипов; при первом запуске модель скачается из интернета.")
        else:
            hint.configure(text="Обводка всегда чёрная; размер — высота текста "
                                "в пикселях на кадре 1080×1920.")

    def _center(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
            y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 4)
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass
