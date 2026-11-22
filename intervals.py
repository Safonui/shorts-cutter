# -*- coding: utf-8 -*-
"""Таймкоды, список клипов и проверка интервалов.

Модуль намеренно не зависит ни от ffmpeg, ни от Tkinter — его можно
проверять отдельно: python -m tests.test_intervals (см. tests/test_intervals.py).

Поддерживаемые форматы ввода (п. 5 ТЗ):
    1:23-2:45            ММ:СС-ММ:СС
    01:23:45-01:25:00    ЧЧ:ММ:СС-ЧЧ:ММ:СС
    00:10 - 00:30        пробелы вокруг дефиса допустимы
    90:00                в форме ММ:СС минуты могут быть больше 59
    95                   просто секунды (для полей «Начало»/«Конец»)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "ParseError", "Clip", "Issue", "INTERVAL_HINT", "TIMECODE_HINT",
    "parse_timecode", "parse_interval", "format_timecode", "format_duration",
    "clip_label", "validate_clips", "clips_total_duration", "issues_summary",
    "split_into_chunks", "split_by_length", "parse_clip_length", "MIN_TAIL_SECONDS",
]

INTERVAL_HINT = "Форматы: 1:23-2:45, 01:23:45-01:25:00, 00:10 - 00:30"
TIMECODE_HINT = "Форматы: 1:23, 01:23:45 или просто секунды (95)"

# Дефис, минус, короткое и длинное тире — всё считаем разделителем интервала.
_SEPARATOR_RE = re.compile(r"\s*[-–—−]\s*")
_NUMBER_RE = re.compile(r"^\d+(?:[.,]\d+)?$")

# Допуск при сравнении границ: полсекунды, чтобы 00:05.0 не считалось выходом за кадр.
BOUNDS_TOLERANCE = 0.5
OVERLAP_EPSILON = 0.001


class ParseError(ValueError):
    """Введённый таймкод или интервал разобрать не удалось."""


# --- Разбор времени -----------------------------------------------------------

def parse_timecode(text: str | float | int) -> float:
    """Разобрать таймкод и вернуть секунды (float).

    Принимает «1:23», «01:23:45», «90:00», «95», «12,5» (запятая как разделитель).
    """
    if isinstance(text, (int, float)):
        value = float(text)
        if value < 0:
            raise ParseError("Время не может быть отрицательным.")
        return value

    raw = str(text).strip().replace(",", ".")
    if not raw:
        raise ParseError("Пустое значение времени.")
    if raw.startswith("-"):
        raise ParseError(f"«{text}» — время не может быть отрицательным.")

    parts = [p.strip() for p in raw.split(":")]
    if len(parts) > 3:
        raise ParseError(f"«{text}» — слишком много двоеточий. {TIMECODE_HINT}")
    if not all(_NUMBER_RE.match(p) for p in parts):
        raise ParseError(f"«{text}» — не похоже на таймкод. {TIMECODE_HINT}")

    values = [float(p) for p in parts]
    if len(parts) == 1:
        seconds = values[0]
    elif len(parts) == 2:
        minutes, secs = values
        if secs >= 60:
            raise ParseError(f"«{text}» — секунды должны быть меньше 60.")
        seconds = minutes * 60 + secs
    else:
        hours, minutes, secs = values
        if minutes >= 60 or secs >= 60:
            raise ParseError(f"«{text}» — минуты и секунды должны быть меньше 60.")
        seconds = hours * 3600 + minutes * 60 + secs
    return float(seconds)


def parse_interval(text: str) -> tuple[float, float]:
    """Разобрать строку вида «1:23-2:45» в пару (начало, конец) в секундах."""
    raw = str(text).strip()
    if not raw:
        raise ParseError("Пустой интервал.")

    parts = _SEPARATOR_RE.split(raw)
    if len(parts) != 2 or not all(p.strip() for p in parts):
        raise ParseError(f"«{text}» — нужен один интервал вида 01:23-02:45. {INTERVAL_HINT}")

    start = parse_timecode(parts[0])
    end = parse_timecode(parts[1])
    return start, end


# --- Клип и его отображение ---------------------------------------------------

@dataclass
class Clip:
    """Один будущий клип: границы в секундах от начала исходного видео."""

    start: float
    end: float
    status: str = "—"          # «готов» появится на этапе 2
    output_path: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def is_valid(self) -> bool:
        return self.end > self.start

    def label(self) -> str:
        return f"{format_timecode(self.start)}–{format_timecode(self.end)}"


def clip_label(index: int, clip: Clip) -> str:
    """Подпись клипа с номером (нумерация с единицы)."""
    return f"№{index + 1}: {clip.label()}"


def format_timecode(seconds: float) -> str:
    """Секунды -> «ММ:СС», а начиная с часа — «ЧЧ:ММ:СС»."""
    total = int(round(max(0.0, float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration(seconds: float) -> str:
    """Секунды -> «12 с» или «12.5 с» для показа в списке клипов."""
    value = max(0.0, float(seconds))
    if abs(value - round(value)) < 0.05:
        return f"{int(round(value))} с"
    return f"{value:.1f} с"


def clips_total_duration(clips: list[Clip]) -> float:
    """Суммарная длительность всех клипов (для статусной строки)."""
    return sum(clip.duration for clip in clips)


# --- «Нарезать подряд» --------------------------------------------------------

MIN_TAIL_SECONDS = 5.0      # хвост короче этого не делаем отдельным клипом


def split_into_chunks(duration: float, chunk_length: float,
                      min_tail: float = MIN_TAIL_SECONDS) -> list[Clip]:
    """Разбить всё видео на равные клипы заданной длины (кнопка «Нарезать подряд»).

    Последний клип может быть короче: он добавляется, только если в нём есть
    смысл (не короче min_tail) — иначе обрезок в 1–2 секунды никому не нужен.
    """
    if duration <= 0 or chunk_length <= 0:
        return []
    clips: list[Clip] = []
    start = 0.0
    while start < duration - 0.05:
        end = min(start + chunk_length, duration)
        # Правило про короткий хвост касается только НЕПОЛНОГО последнего клипа:
        # полный клип нужной длины оставляем, даже если он короче min_tail
        # (иначе видео 6 с с клипами по 2 с давало бы два клипа вместо трёх).
        is_partial = (end - start) < chunk_length - 0.001
        if is_partial and clips and (end - start) < min_tail:
            break
        clips.append(Clip(start=round(start, 3), end=round(end, 3)))
        start = end
    return clips


# --- «Разделить по длине» -----------------------------------------------------

def split_by_length(duration: float, length: float,
                    min_tail: float = MIN_TAIL_SECONDS) -> list[Clip]:
    """Разбить видео на отрезки заданной длины (кнопка «Разделить по длине»).

    Отличие от `split_into_chunks`: видео размечается целиком. Последний отрезок —
    остаток какой угодно длины; если он короче min_tail, он присоединяется к
    предыдущему отрезку, а не выбрасывается.

    Пример: видео 20:40 (1240 с) по 300 с → 0–300, 300–600, 600–900, 900–1240.
    """
    if duration <= 0 or length <= 0:
        return []
    clips: list[Clip] = []
    start = 0.0
    while start < duration - 0.05:
        end = min(start + length, duration)
        clips.append(Clip(start=round(start, 3), end=round(end, 3)))
        start = end

    # Короткий остаток сливаем с предыдущим отрезком: отдельный обрезок в 1–2
    # секунды для публикации бесполезен, а терять его нельзя.
    if len(clips) >= 2 and clips[-1].duration < min_tail - 0.001:
        tail = clips.pop()
        previous = clips[-1]
        clips[-1] = Clip(start=previous.start, end=tail.end)
    return clips


def parse_clip_length(text: str, duration: float | None = None) -> int:
    """Проверить «длину клипа» для кнопки «Разделить по длине».

    Требования: целое положительное число секунд, не больше длительности видео.
    Возвращает длину в секундах; при ошибке бросает ParseError с понятным текстом.
    """
    raw = (text or "").strip()
    value = raw.replace(",", ".")
    if not value:
        raise ParseError("Укажите длину клипа в секундах, например 300.")
    if value.startswith(("-", "−", "–", "—")):
        raise ParseError("Длина клипа должна быть больше нуля.")
    if not _NUMBER_RE.match(value):
        raise ParseError(f"«{raw}» — это не число. Укажите длину клипа целым "
                         "числом секунд, например 300.")
    number = float(value)
    if number != int(number):
        raise ParseError(f"«{raw}» — не целое число. Длина клипа задаётся целым "
                         "числом секунд, например 300.")
    seconds = int(number)
    if seconds <= 0:
        raise ParseError("Длина клипа должна быть больше нуля.")
    if duration is not None and seconds > duration + BOUNDS_TOLERANCE:
        raise ParseError(f"Длина клипа ({seconds} с) больше самого видео "
                         f"({format_duration(duration)}). Укажите число не больше "
                         "длительности видео.")
    return seconds


# --- Проверка интервалов ------------------------------------------------------

@dataclass
class Issue:
    """Найденная проблема. index = -1 означает «не относится к строке списка»."""

    index: int
    message: str
    severity: str = "error"        # "error" — ошибка, "warning" — предупреждение
    clip_indexes: list[int] = field(default_factory=list)


def validate_clips(clips: list[Clip], duration: float | None = None) -> list[Issue]:
    """Проверить интервалы: границы, выход за пределы видео, пересечения.

    duration — длительность исходного видео в секундах; если None, проверка
    границ пропускается и добавляется предупреждение.
    """
    issues: list[Issue] = []

    if duration is None:
        issues.append(Issue(-1, "Длительность видео неизвестна — границы не проверены.",
                            severity="warning"))

    for index, clip in enumerate(clips):
        if not clip.is_valid:
            issues.append(Issue(
                index,
                f"Клип №{index + 1}: конец ({format_timecode(clip.end)}) не позже начала "
                f"({format_timecode(clip.start)}).",
                clip_indexes=[index]))
            continue
        if duration is None:
            continue
        if clip.start >= duration:
            issues.append(Issue(
                index,
                f"Клип №{index + 1}: начало ({format_timecode(clip.start)}) за пределами видео "
                f"(длительность {format_timecode(duration)}).",
                clip_indexes=[index]))
        elif clip.end > duration + BOUNDS_TOLERANCE:
            issues.append(Issue(
                index,
                f"Клип №{index + 1}: конец ({format_timecode(clip.end)}) выходит за пределы видео "
                f"(длительность {format_timecode(duration)}).",
                clip_indexes=[index]))

    # Пересечения: список клипов короткий, поэтому честно сверяем все пары —
    # так помечаются все участники, даже если один клип накрывает несколько других.
    for left in range(len(clips)):
        for right in range(left + 1, len(clips)):
            first, second = clips[left], clips[right]
            if not (first.is_valid and second.is_valid):
                continue
            overlaps = (second.start < first.end - OVERLAP_EPSILON
                        and first.start < second.end - OVERLAP_EPSILON)
            if overlaps:
                issues.append(Issue(
                    left,
                    f"Клип №{left + 1} пересекается с клипом №{right + 1}.",
                    clip_indexes=[left, right]))
                issues.append(Issue(
                    right,
                    f"Клип №{right + 1} пересекается с клипом №{left + 1}.",
                    clip_indexes=[left, right]))
    return issues


def issues_summary(issues: list[Issue]) -> str:
    """Текст для диалога после нажатия «Проверить интервалы»."""
    if not issues:
        return "Интервалы корректны: ошибок и пересечений не найдено."
    lines = []
    for issue in issues:
        prefix = "!" if issue.severity == "warning" else "•"
        lines.append(f"{prefix} {issue.message}")
    return "\n".join(lines)
