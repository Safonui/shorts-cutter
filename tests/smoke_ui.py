# -*- coding: utf-8 -*-
"""Автопроверка интерфейса без участия человека.

Запуск:  python tests/smoke_ui.py
Окно создаётся и сразу скрывается, поэтому проверка проходит без кликов.
Скрипт ловит такие ошибки вёрстки, как «таблица клипов сжалась до 1×1»,
и проверяет цвета темы, подсветку ошибок и тёмные диалоги.
Юнит-тесты парсера лежат отдельно: python main.py --selftest
"""

from __future__ import annotations

import os
import shutil
import sys
import tkinter as tk
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dialogs import ask_yes_no, show_message  # noqa: E402
from intervals import Clip, validate_clips  # noqa: E402
from appconfig import (APP_VERSION, config_path, load_settings)  # noqa: E402
from subtitles import is_available  # noqa: E402
from ui import asset_path  # noqa: E402
from theme import COLORS  # noqa: E402
from ui import ShortsCutterApp  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]

checks: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    checks.append(f"{'OK  ' if condition else 'FAIL'} {name} {extra}")


def _luma(color: str) -> float:
    """Яркость цвета 0…1 — чтобы проверять «нет светлых элементов»."""
    red, green, blue = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


app = ShortsCutterApp()
app.withdraw()
app.update()

check("фон окна = фон темы", str(app.cget("background")).upper() == COLORS["bg"].upper(),
      f"{app.cget('background')} / {COLORS['bg']}")
check("фон темы тёмный (нет светлых элементов)", _luma(COLORS["bg"]) < 0.15,
      f"яркость {_luma(COLORS['bg']):.3f}")
minimum = app.minsize()
check("минимальный размер не меньше 1000×780",
      minimum[0] >= 1000 and minimum[1] >= 780, str(minimum))
check("кнопки нарезки неактивны",
      "disabled" in app.button_cut.state() and "disabled" in app.button_cancel.state())
check("таблица клипов не сжата (окно скрыто в тесте)",
      app.tree.winfo_width() > 600 and app.tree.winfo_height() >= 140,
      f"{app.tree.winfo_width()}x{app.tree.winfo_height()}")
check("сводка настроек заполнена",
      "Вертикаль" in app.var_export_summary.get() and "CRF 20" in app.var_export_summary.get(),
      app.var_export_summary.get())
app._draw_hero(type("Event", (), {"width": 1000, "height": 104})())
check("градиентная шапка нарисована",
      len(app.hero.find_withtag("gradient")) > 20 and len(app.hero.find_withtag("hero_text")) >= 2,
      f"{len(app.hero.find_withtag('gradient'))} линий")

# --- окно настроек экспорта -----------------------------------------------------
app._on_open_settings()
app.update()
settings = app._settings_window
check("окно настроек открылось", settings.winfo_exists()
      and settings.winfo_reqwidth() > 400 and settings.winfo_reqheight() > 300,
      f"{settings.winfo_reqwidth()}x{settings.winfo_reqheight()}")
check("повторный вызов не дублирует окно", (app._on_open_settings(),
      sum(1 for w in app.winfo_children() if w.winfo_class() == "Toplevel"))[1] == 1)
check("настройки в цвете фона темы",
      str(settings.cget("background")).upper() == COLORS["bg"].upper())
def _find_widgets(widget, class_name: str, found=None):
    """Рекурсивно собрать виджеты нужного класса."""
    found = [] if found is None else found
    for child in widget.winfo_children():
        if child.winfo_class() == class_name:
            found.append(child)
        _find_widgets(child, class_name, found)
    return found


face_checks = [w for w in _find_widgets(settings, "TCheckbutton")
               if "позже" in str(w.cget("text"))]
check("автодетект лица серый с подписью «позже»",
      bool(face_checks) and all("disabled" in w.state() for w in face_checks),
      str([str(w.cget("text")) for w in face_checks]))
check("формат вывода активен (этап 2)",
      any("disabled" not in w.state() for w in _find_widgets(settings, "TCombobox")),
      str([str(w.state()) for w in _find_widgets(settings, "TCombobox")]))
check("качество (CRF) активно (этап 2)",
      any("disabled" not in w.state() for w in _find_widgets(settings, "TSpinbox")))
subs_checks = [w for w in _find_widgets(settings, "TCheckbutton")
               if "субтитры" in str(w.cget("text")).lower()]
hint_widget = getattr(settings, "subs_hint", None)
hint_text = str(hint_widget.cget("text")) if hint_widget is not None else ""
if is_available():
    check("галочка субтитров активна (модуль установлен)",
          bool(subs_checks) and any("disabled" not in w.state() for w in subs_checks),
          str([str(w.state()) for w in subs_checks]))
else:
    check("галочка субтитров серая без faster-whisper",
          bool(subs_checks) and all("disabled" in w.state() for w in subs_checks),
          str([str(w.state()) for w in subs_checks]))
    check("рядом объяснение, как включить субтитры",
          "faster-whisper" in hint_text, hint_text)
check("поля оформления субтитров недоступны при выключенных субтитрах",
      all("disabled" in w.state() for w in getattr(settings, "subs_controls", [])),
      str([str(w.state()) for w in getattr(settings, "subs_controls", [])]))
settings.destroy()
app.update_idletasks()
check("главное окно не пострадало от закрытия настроек",
      app.tree.winfo_height() > 100, str(app.tree.winfo_height()))

# --- добавление интервалов через строку и через поля -------------------------
app.var_interval.set("0:30-0:45")
app._on_add_interval()
app.var_interval.set(" 1:05:00-1:06:00 ")
app._on_add_interval()
app.var_start.set("00:10")
app.var_end.set("00:20")
app._on_add_current_interval()

check("клипов добавлено 3", len(app.clips) == 3, str([c.label() for c in app.clips]))
check("поля очищены после добавления",
      app.var_interval.get() == "" and app.var_end.get() == "")
check("строки в таблице", len(app.tree.get_children()) == 3)
check("значения строки 1",
      tuple(app.tree.item("0", "values"))[:3] == ("1", "00:30–00:45", "15 с"),
      str(app.tree.item("0", "values")))
check("сводка по клипам", app.var_clips_summary.get().startswith("Клипов: 3"),
      app.var_clips_summary.get())

# --- доступность кнопки «Нарезать» ---------------------------------------------
_saved_info = app.video_info
app.video_info = type("Info", (), {"duration": 600.0})()
app._update_action_buttons()
check("«Нарезать» активна, когда есть видео и клипы",
      "disabled" not in app.button_cut.state(), str(app.button_cut.state()))
app.video_info = _saved_info
app._update_action_buttons()
check("без видео «Нарезать» неактивна", "disabled" in app.button_cut.state())

# --- ошибка: конец раньше начала ---------------------------------------------
app.var_interval.set("13:20-12:30")
app._on_add_interval()
check("битый интервал добавлен как ошибочный", len(app.clips) == 4 and not app.clips[3].is_valid)
check("строка подсвечена как ошибка", "error" in app.tree.item("3", "tags"),
      str(app.tree.item("3", "tags")))

# --- пересечение интервалов ---------------------------------------------------
app.var_interval.set("0:40-0:50")
app._on_add_interval()
issues = validate_clips(app.clips, duration=None)
errors = [i for i in issues if i.severity == "error"]
check("найдено 3 сообщения об ошибках (границы + пересечение с двух сторон)",
      len(errors) == 3, str([i.message for i in errors]))
check("пересечение помечает обе строки",
      "error" in app.tree.item("0", "tags") and "error" in app.tree.item("4", "tags"),
      f"{app.tree.item('0', 'tags')} / {app.tree.item('4', 'tags')}")

# --- проверка границ при известной длительности --------------------------------
app.video_info = type("Info", (), {"duration": 60.0})()  # видео длиной 1 минута
issues = app._validate(mark_rows=True)
messages = [i.message for i in issues if i.severity == "error"]
check("выход за пределы видео найден",
      any("за пределами видео" in m for m in messages), str(messages))
check("внутри границ ошибки нет", not any("00:30–00:45" in m for m in messages))

# --- удаление и очистка --------------------------------------------------------
app.tree.selection_set("0")
app._on_delete_selected()
check("клип удалён", len(app.clips) == 4, str(len(app.clips)))


def _close_top_levels(value=True):
    for widget in app.winfo_children():
        if isinstance(widget, tk.Toplevel):
            try:
                widget._close(value)
            except tk.TclError:
                widget.destroy()


app.after(150, _close_top_levels)
app._on_clear_clips()
check("список очищен после подтверждения", app.clips == [], str(app.clips))

# --- субтитры без установленного модуля (критерий приёмки № 7) -------------------
if not is_available():
    app.var_subtitles.set(1)
    app.after(150, _close_top_levels)          # закрыть окно с объяснением
    check("без faster-whisper задача субтитров не создаётся",
          app._prepare_subtitle_task(Path(".")) is None)
    check("внутренняя задача субтитров не осталась", app._subtitle_task is None)
    app.var_subtitles.set(0)
    app._update_export_summary()
check("в сводке настроек видно состояние субтитров",
      "субтитры" in app.var_export_summary.get(), app.var_export_summary.get())

# --- «Нарезать подряд» (этап 4) --------------------------------------------------
check("поле длины клипа заполнено значением по умолчанию",
      app.var_chunk_len.get().strip() == "60", app.var_chunk_len.get())
check("кнопка «Нарезать подряд» активна",
      "disabled" not in app.button_chunk.state())

_saved_clips, _saved_info = app.clips, app.video_info
app.clips = []
app.video_info = type("Info", (), {"duration": 600.0, "path": Path("видео.mp4")})()
app.var_chunk_len.set("120")
app._on_split_into_chunks()                       # пустой список — без вопросов
check("«Нарезать подряд» разбило видео на равные клипы", len(app.clips) == 5,
      f"клипов: {len(app.clips)}")
check("длительность клипов равна заданной",
      all(abs(clip.duration - 120.0) < 0.01 for clip in app.clips),
      str([clip.duration for clip in app.clips]))
check("клипы покрывают всё видео", abs(app.clips[-1].end - 600.0) < 0.01,
      str(app.clips[-1].end))

app.after(150, lambda: _close_top_levels(None))   # «Отмена» в вопросе о списке
app._on_split_into_chunks()
check("отмена вопроса оставляет список как был", len(app.clips) == 5,
      f"клипов: {len(app.clips)}")


def _close_with(value):
    for widget in app.winfo_children():
        if isinstance(widget, tk.Toplevel):
            try:
                widget._close(value)
            except Exception:
                widget.destroy()


app.var_chunk_len.set("300")
app.after(150, lambda: _close_with("replace"))
app._on_split_into_chunks()
check("«Заменить список» оставляет два клипа",
      len(app.clips) == 2 and abs(app.clips[0].duration - 300.0) < 0.01,
      f"клипов: {len(app.clips)}")

app.video_info = None
app.after(150, _close_top_levels)
app._on_split_into_chunks()
check("без видео «Нарезать подряд» ничего не делает", len(app.clips) == 2)

app.clips, app.video_info = _saved_clips, None      # поддельное видео убираем
app._validate(mark_rows=True)
app._update_action_buttons()

# --- поиск ярких моментов (расширение сверх ТЗ) ----------------------------------
check("кнопка «Найти яркие моменты» есть и активна",
      "disabled" not in app.button_highlights.state())
check("число моментов по умолчанию — 5",
      app.var_highlight_count.get().strip() == "5", app.var_highlight_count.get())
app.after(150, _close_top_levels)
app._on_find_highlights()             # видео не выбрано → только сообщение
check("без видео поиск моментов не запускается",
      app._analyzing is False and app._highlight_cancel is None)
check("фоновые задачи не остались",
      app._cutter is None and app._subtitle_task is None)

# --- полноценное приложение: настройки, иконка, «О программе» --------------------
check("иконка окна найдена", asset_path("icon.ico") is not None,
      str(asset_path("icon.ico")))
check("полоска прогресса в панели задач создана", app.taskbar is not None)
check("версия приложения известна", bool(APP_VERSION) and APP_VERSION.count(".") == 2,
      APP_VERSION)

_cfg_dir = PROJECT_ROOT / "_tmp_smoke_cfg"
shutil.rmtree(_cfg_dir, ignore_errors=True)
_cfg_dir.mkdir(parents=True, exist_ok=True)
with mock.patch.dict(os.environ, {"APPDATA": str(_cfg_dir)}):
    app.var_crf.set("27")
    app.var_chunk_len.set("42")
    app.var_highlight_count.set("7")
    app._save_settings_now()
    saved_file = config_path()
    check("настройки записались в файл", saved_file.exists(), str(saved_file))
    saved = load_settings() if saved_file.exists() else {}
    check("сохранённые значения читаются обратно",
          saved.get("crf") == "27" and saved.get("chunk_len") == "42"
          and saved.get("highlight_count") == "7",
          f"crf={saved.get('crf')}, chunk={saved.get('chunk_len')}, "
          f"moments={saved.get('highlight_count')}")
    # геометрия и папка последнего видео тоже сохраняются
    check("в настройках есть размер окна", bool(saved.get("geometry")),
          str(saved.get("geometry")))
shutil.rmtree(_cfg_dir, ignore_errors=True)

app.after(150, _close_top_levels)
app._on_show_about()
check("окно «О программе» открывается и закрывается", True)

# --- тёмные диалоги -------------------------------------------------------------
app.after(150, _close_top_levels)
show_message(app, "Проверка", "Тестовое сообщение")
check("диалог сообщения открылся и закрылся", True)

app.after(150, lambda: _close_top_levels(True))
answer_yes = ask_yes_no(app, "Проверка", "Ответ да?")
app.after(150, lambda: _close_top_levels(False))
answer_no = ask_yes_no(app, "Проверка", "Ответ нет?")
check("ask_yes_no возвращает True/False", answer_yes is True and answer_no is False,
      f"{answer_yes}/{answer_no}")

# --- внешний вид ttk ------------------------------------------------------------
from tkinter import ttk  # noqa: E402
style = ttk.Style(app)
check("стиль Treeview = цвет панели темы",
      str(style.lookup("Treeview", "background")).upper() == COLORS["panel"].upper(),
      str(style.lookup("Treeview", "background")))
check("таймкоды моноширинным шрифтом (Consolas)",
      "Consolas" in str(style.lookup("Treeview", "font"))
      and "Consolas" in str(style.lookup("Time.TEntry", "font")),
      f"{style.lookup('Treeview', 'font')} / {style.lookup('Time.TEntry', 'font')}")
check("кнопки «Нарезать»/«Отмена»: неоновая рамка и подпись",
      str(style.lookup("Accent.TButton", "bordercolor")).upper() == COLORS["neon_pink"].upper()
      and str(style.lookup("Accent.TButton", "foreground")).upper() == COLORS["neon_pink"].upper(),
      f"{style.lookup('Accent.TButton', 'bordercolor')} / "
      f"{style.lookup('Accent.TButton', 'foreground')}")
check("наведение на акцентную кнопку меняет цвет на яркий",
      any(entry[1].upper() == COLORS["neon_pink_hot"].upper()
          for entry in style.map("Accent.TButton", "bordercolor")),
      str(style.map("Accent.TButton", "bordercolor")))
check("выбранная строка списка — фиолетовая",
      any(entry[1].upper() == COLORS["select"].upper()
          for entry in style.map("Treeview", "background")),
      str(style.map("Treeview", "background")))
check("фон строки с ошибкой = цвет темы",
      str(app.tree.tag_configure("error", "background")).upper() == COLORS["error_bg"].upper(),
      str(app.tree.tag_configure("error", "background")))


def _find_section_titles(widget, found=None):
    """Собрать заголовки секций: стиль Section.TLabel."""
    found = [] if found is None else found
    for child in widget.winfo_children():
        if child.winfo_class() == "TLabel" and str(child.cget("style")) == "Section.TLabel":
            found.append(str(child.cget("text")))
        _find_section_titles(child, found)
    return found


titles = _find_section_titles(app)
check("заголовки секций капсом", bool(titles) and all(t == t.upper() for t in titles),
      str(titles))
check("шрифт Segoe UI", "Segoe UI" in str(style.lookup("TLabel", "font")),
      str(style.lookup("TLabel", "font")))
check("тема ttk clam", style.theme_use() == "clam", style.theme_use())

# --- незакрытые модальные окна не остались --------------------------------------
check("нет висящих диалогов", not any(isinstance(w, tk.Toplevel) for w in app.winfo_children()))

app.destroy()

print("\n".join(checks))
failed = [line for line in checks if line.startswith("FAIL")]
print(f"\nИтого: {len(checks) - len(failed)}/{len(checks)} проверок пройдено")
sys.exit(1 if failed else 0)
