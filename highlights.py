# -*- coding: utf-8 -*-
"""Поиск ярких моментов в видео по звуку (расширение сверх ТЗ, этап 5).

Идея: интересные места (смех, крик, аплодисменты, резкая смена громкости, напор
в речи) хорошо видны по звуковой дорожке. Видео декодируется ffmpeg в моно
16 кГц, режется на окна по 0.5 с, для каждого окна считаются три признака:

* **громкость** — среднеквадратичная амплитуда (RMS);
* **всплеск** — насколько окно громче предыдущего (смех, удар, крик);
* **высокие частоты** — доля быстрых колебаний (крик и смех «ярче» ровного голоса).

Признаки нормируются на медиану и медианное отклонение всего видео (устойчиво к
записям с разным уровнем), складываются в оценку, сглаживаются и по локальным
максимумам выбираются клипы нужной длины. Ничего не «понимает» содержание: это
акустика, а не смысл — зато работает офлайн, быстро и без тяжёлых моделей.

numpy используется, если он есть (быстрее); без него работает то же самое на
чистом Python.
"""

from __future__ import annotations

import logging
import statistics
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from cutting import CREATE_NO_WINDOW, CutError, VideoInfo, ffmpeg_exe
from intervals import Clip

__all__ = ["HighlightError", "HighlightSettings", "Window", "Moment",
           "analyze_audio", "score_windows", "pick_moments", "find_highlights"]

log = logging.getLogger("shorts")

SAMPLE_RATE = 16000          # моно 16 кГц — хватает для акустики и совпадает с распознаванием
WINDOW_SECONDS = 0.5
READ_CHUNK = 1 << 20         # 1 МБ сырых сэмплов за раз (~32 с звука)

# Веса признаков в итоговой оценке.
WEIGHT_LOUDNESS = 0.50
WEIGHT_SPIKE = 0.35
WEIGHT_HIGH = 0.15


class HighlightError(RuntimeError):
    """Не удалось проанализировать звук."""


class HighlightCancelled(RuntimeError):
    """Анализ прерван пользователем."""


@dataclass
class HighlightSettings:
    """Параметры поиска ярких моментов."""

    clip_length: float = 30.0     # желаемая длина клипа, секунды
    max_clips: int = 5            # сколько моментов вернуть
    sensitivity: float = 1.0      # больше — строже отбор (порог выше)
    min_gap: float = 1.0          # минимальный промежуток между клипами


@dataclass
class Window:
    """Окно анализа: время, признаки и итоговая оценка."""

    start: float
    rms: float = 0.0
    spike: float = 0.0
    high: float = 0.0
    score: float = 0.0


@dataclass
class Moment:
    """Найденный яркий момент."""

    clip: Clip
    score: float
    loudness_ratio: float = 1.0
    spike_ratio: float = 1.0
    high_ratio: float = 1.0
    reasons: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return ", ".join(self.reasons) if self.reasons else "выраженный звук"


# --- Признаки окон ------------------------------------------------------------

def _features_numpy(data: bytes, numpy) -> tuple[float, float]:
    """RMS и «высокочастотность» куска звука (numpy-путь)."""
    samples = numpy.frombuffer(data, dtype=numpy.int16).astype(numpy.float32) / 32768.0
    if samples.size == 0:
        return 0.0, 0.0
    rms = float(numpy.sqrt(numpy.mean(samples * samples)))
    if samples.size > 1:
        diff = numpy.diff(samples)
        high = float(numpy.sqrt(numpy.mean(diff * diff)))
    else:
        high = 0.0
    return rms, high / (rms + 1e-6)


def _features_python(data: bytes) -> tuple[float, float]:
    """То же самое без numpy — медленнее, но работает всегда."""
    import array

    samples = array.array("h")
    samples.frombytes(data)
    if not samples:
        return 0.0, 0.0
    total = 0.0
    for value in samples:
        total += value * value
    rms = (total / len(samples)) ** 0.5 / 32768.0

    diff_total = 0.0
    previous = samples[0]
    for value in samples:
        delta = value - previous
        diff_total += delta * delta
        previous = value
    high = (diff_total / len(samples)) ** 0.5 / 32768.0
    return rms, high / (rms + 1e-6)


def analyze_audio(info: VideoInfo, on_progress: Callable[[float], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None,
                  window_seconds: float = WINDOW_SECONDS) -> list[Window]:
    """Прочитать звук и вернуть окна с признаками.

    Звук декодируется ffmpeg и разбирается по мере поступления, поэтому память
    не зависит от длительности видео (4 часа не займут гигабайты).
    """
    try:
        import numpy                      # noqa: F401
        features = lambda chunk: _features_numpy(chunk, numpy)  # noqa: E731
        backend = "numpy"
    except ImportError:
        features = _features_python
        backend = "python"
    log.info("Анализ звука: %s, окна по %.2f с", backend, window_seconds)

    if not info.has_audio:
        raise HighlightError("в файле нет звуковой дорожки — анализировать нечего")

    command = [
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(info.path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-",
    ]
    log.info("Команда: %s", subprocess.list2cmdline(command))
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,
                                   creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError as exc:
        raise HighlightError(f"не найден ffmpeg: {command[0]}") from exc

    window_bytes = int(window_seconds * SAMPLE_RATE) * 2
    total_bytes = max(1, int(info.duration * SAMPLE_RATE) * 2) if info.duration else 0
    windows: list[Window] = []
    buffer = b""
    read_total = 0
    position = 0.0
    reported = -1.0
    cancelled = False

    def report(processed: int, force: bool = False) -> None:
        """Сообщать о ходе анализа, но не чаще одного раза на процент."""
        nonlocal reported
        if on_progress is None or not total_bytes:
            return
        percent = min(99.0, processed / total_bytes * 100.0)
        if force or percent - reported >= 1.0:
            reported = percent
            on_progress(percent)

    try:
        assert process.stdout is not None
        while True:
            if should_cancel is not None and should_cancel():
                cancelled = True
                break
            chunk = process.stdout.read(READ_CHUNK)
            if not chunk:
                break
            buffer += chunk
            read_total += len(chunk)
            while len(buffer) >= window_bytes:
                piece, buffer = buffer[:window_bytes], buffer[window_bytes:]
                rms, high = features(piece)
                windows.append(Window(start=position, rms=rms, high=high))
                position += window_seconds
                # Прогресс считаем по уже разобранным окнам: для короткого ролика
                # иначе выходила бы всего пара точек.
                report(read_total - len(buffer))
        if buffer and not cancelled:
            rms, high = features(buffer)
            windows.append(Window(start=position, rms=rms, high=high))
            position += len(buffer) / (SAMPLE_RATE * 2)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    if cancelled:
        raise HighlightCancelled()
    if not windows:
        if process.returncode not in (0, None):
            raise HighlightError("ffmpeg не смог прочитать звук из этого файла")
        raise HighlightError("в файле не нашлось звука для анализа")
    if on_progress is not None:
        on_progress(100.0)
    return windows


# --- Оценка и выбор моментов ---------------------------------------------------

def _robust_scale(values: list[float]) -> tuple[float, float]:
    """Медиана и устойчивый разброс (медианное отклонение)."""
    median = statistics.median(values) if values else 0.0
    deviations = [abs(value - median) for value in values] or [0.0]
    spread = statistics.median(deviations)
    if spread <= 1e-9:
        spread = max(1e-6, (max(values) - min(values)) / 6 if values else 1e-6)
    return median, spread


def score_windows(windows: list[Window], smooth_radius: int = 2) -> list[Window]:
    """Посчитать оценку «яркости» каждого окна и сгладить её."""
    if not windows:
        return windows
    rms_median, rms_spread = _robust_scale([w.rms for w in windows])
    high_median, high_spread = _robust_scale([w.high for w in windows])

    for index, window in enumerate(windows):
        loudness = max(0.0, (window.rms - rms_median) / (3 * rms_spread))
        previous = windows[index - 1].rms if index else window.rms
        spike = max(0.0, (window.rms - previous) / (3 * rms_spread))
        high = max(0.0, (window.high - high_median) / (3 * high_spread))
        window.spike = spike
        window.score = (WEIGHT_LOUDNESS * loudness + WEIGHT_SPIKE * spike
                        + WEIGHT_HIGH * high)

    # Сглаживание: одиночный громкий кадр не должен решать всё, но всплеск
    # из двух-трёх окон сохраняется.
    if smooth_radius > 0:
        raw = [w.score for w in windows]
        for index, window in enumerate(windows):
            left = max(0, index - smooth_radius)
            right = min(len(raw), index + smooth_radius + 1)
            window.score = sum(raw[left:right]) / (right - left)
    return windows


def pick_moments(windows: list[Window], duration: float,
                 settings: HighlightSettings) -> list[Moment]:
    """Выбрать яркие моменты: локальные максимумы выше порога, без пересечений."""
    if not windows or duration <= 0:
        return []
    scores = [w.score for w in windows]
    mean = statistics.fmean(scores)
    spread = statistics.pstdev(scores) or 1e-6
    threshold = mean + max(0.15, settings.sensitivity) * spread

    peaks: list[int] = []
    for index in range(len(windows)):
        current = windows[index].score
        if current < threshold:
            continue
        left = windows[index - 1].score if index else -1.0
        right = windows[index + 1].score if index + 1 < len(windows) else -1.0
        if current >= left and current > right:
            peaks.append(index)
    peaks.sort(key=lambda index: -windows[index].score)

    clip_length = max(1.0, min(settings.clip_length, duration))
    moments: list[Moment] = []
    for index in peaks:
        if len(moments) >= max(1, settings.max_clips):
            break
        window = windows[index]
        center = window.start + WINDOW_SECONDS / 2
        start = max(0.0, min(center - clip_length / 2, duration - clip_length))
        clip = Clip(start=round(start, 2), end=round(min(start + clip_length, duration), 2))
        if any(clip.start < other.clip.end + settings.min_gap
               and other.clip.start < clip.end + settings.min_gap for other in moments):
            continue
        rms_median, _ = _robust_scale([w.rms for w in windows])
        high_median, _ = _robust_scale([w.high for w in windows])
        moment = Moment(clip=clip, score=window.score)
        moment.loudness_ratio = window.rms / rms_median if rms_median else 1.0
        moment.spike_ratio = 1.0 + window.spike
        moment.high_ratio = window.high / high_median if high_median else 1.0
        moment.reasons = _describe(moment)
        moments.append(moment)
    moments.sort(key=lambda item: item.clip.start)
    return moments


def _describe(moment: Moment) -> list[str]:
    """Объяснить словами, почему момент попал в список."""
    reasons: list[str] = []
    if moment.loudness_ratio >= 1.6:
        reasons.append(f"громче среднего в {moment.loudness_ratio:.1f} раза")
    if moment.spike_ratio >= 1.5:
        reasons.append("резкий всплеск звука")
    if moment.high_ratio >= 1.4:
        reasons.append("много высоких частот (смех, крик)")
    if not reasons:
        reasons.append("заметное место по звуку")
    return reasons


def find_highlights(info: VideoInfo, settings: HighlightSettings,
                    on_progress: Callable[[float], None] | None = None,
                    should_cancel: Callable[[], bool] | None = None,
                    on_stage: Callable[[str], None] | None = None) -> list[Moment]:
    """Полный проход: анализ звука, оценка, выбор моментов."""
    if on_stage is not None:
        on_stage("audio")
    windows = analyze_audio(info, on_progress=on_progress, should_cancel=should_cancel)
    if should_cancel is not None and should_cancel():
        raise HighlightCancelled()
    if on_stage is not None:
        on_stage("scoring")
    score_windows(windows)
    moments = pick_moments(windows, info.duration, settings)
    log.info("Найдено ярких моментов: %d из %d окон", len(moments), len(windows))
    return moments
