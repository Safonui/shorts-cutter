# -*- coding: utf-8 -*-
"""Субтитры: распознавание речи и вшивание текста в клип (этап 3 ТЗ).

Модуль опционален. Если пакет faster-whisper не установлен, приложение
продолжает резать клипы, а функции субтитров отключаются с понятным сообщением
(п. 2 и п. 6 ТЗ, критерий приёмки № 7).

Как это работает:
1. Из исходного видео вырезается звук нужного интервала (ffmpeg → WAV 16 кГц моно).
2. faster-whisper распознаёт речь и отдаёт сегменты с таймкодами.
3. Сегменты пишутся во временный .srt, который вшивается в клип фильтром
   `subtitles` с оформлением (размер, положение, цвет, чёрная обводка).
4. Временный .srt удаляется после нарезки.

Модель `small` при первом запуске скачивается из интернета, дальше работает
офлайн; на CPU распознавание идёт примерно 1–3× длительности звука, о чём
интерфейс предупреждает до старта (п. 9 ТЗ).
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from cutting import CREATE_NO_WINDOW, VideoInfo, escape_filter_path, ffmpeg_exe

__all__ = [
    "SubtitleError", "SubtitleUnavailable", "SubtitleStyle", "SubtitleTask",
    "MODEL_SIZE", "is_available", "unavailable_reason", "estimate_seconds",
    "format_timestamp", "build_srt", "write_srt", "ass_color", "force_style",
    "subtitle_filter",
]

log = logging.getLogger("shorts")

MODEL_SIZE = "small"
PLAY_RES_Y = 288          # libass подставляет это разрешение для .srt
BASE_HEIGHT = 1920        # на таком кадре размер шрифта из настроек = его пикселям
SPEED_FACTOR = (1.0, 3.0)  # ожидаемая скорость распознавания на CPU
INSTALL_HINT = ("Модуль субтитров не установлен.\n\n"
                "Выполните в командной строке:\n"
                "    pip install faster-whisper\n\n"
                "Нарезка без субтитров работает и без него.")

_model = None
_model_size_loaded: str | None = None


class SubtitleError(RuntimeError):
    """Не удалось распознать речь или подготовить субтитры."""


class SubtitleUnavailable(SubtitleError):
    """Пакет faster-whisper не установлен — субтитры отключены."""


# --- Доступность модуля -------------------------------------------------------

def is_available() -> bool:
    """Установлен ли faster-whisper (без импорта тяжёлых зависимостей)."""
    if importlib.util.find_spec("faster_whisper") is None:
        return False
    return True


def unavailable_reason() -> str:
    """Понятное объяснение, почему субтитры недоступны."""
    if is_available():
        return ""
    if getattr(sys, "frozen", False):     # собранный .exe без модуля распознавания
        return ("В этой сборке (.exe) субтитры не включены: для распознавания речи\n"
                "нужен пакет faster-whisper, а он в сборку не входит.\n\n"
                "Нарезка, кроп 9:16, отмена и «нарезать подряд» работают полностью.\n"
                "Чтобы получить субтитры, запустите версию из исходников (ЗАПУСК.bat) —\n"
                "там модуль распознавания уже установлен.")
    return INSTALL_HINT


def estimate_seconds(duration: float) -> tuple[float, float]:
    """Оценка времени распознавания: от и до (1× и 3× длительности)."""
    return duration * SPEED_FACTOR[0], duration * SPEED_FACTOR[1]


# --- Оформление и текст -------------------------------------------------------

@dataclass
class SubtitleStyle:
    """Настройки субтитров из п. 3.3 ТЗ."""

    font_size: int = 48          # высота текста в пикселях на кадре 1080×1920
    position: str = "низ"        # «низ» или «центр»
    color: str = "белый"         # «белый» или «жёлтый»


COLOR_RGB = {"белый": "#FFFFFF", "жёлтый": "#FFFF00"}


def ass_color(hex_rgb: str) -> str:
    """#RRGGBB → &H00BBGGRR: в ASS цвета хранятся в порядке BGR."""
    red, green, blue = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H00{blue}{green}{red}".upper()


def force_style(style: SubtitleStyle) -> str:
    """Строка force_style для фильтра ffmpeg `subtitles`.

    Размер пересчитывается из пикселей кадра 1080×1920 в систему libass
    (по умолчанию 384×288), иначе 48 дало бы текст высотой в треть кадра.
    """
    font_size = max(4, round(style.font_size * PLAY_RES_Y / BASE_HEIGHT))
    # Нумерация выравниваний в .srt-сценариях — устаревшая (ASS v4): 2 — низ по
    # центру, 10 — середина по центру. Значение 5 здесь означало бы «сверху слева».
    alignment = 2 if style.position.strip().lower() != "центр" else 10
    # Отступ тоже в системе libass (384×288): 22 — это ≈150 px на кадре 1080×1920,
    # то есть 8 % высоты. При 60 текст уезжал на 400 px от низа, как показала
    # проверка кадра.
    margin = 22 if alignment == 2 else 0
    color = ass_color(COLOR_RGB.get(style.color.strip().lower(), COLOR_RGB["белый"]))
    # Боковые поля тоже в системе libass: 25 — это ≈70 px с каждой стороны
    # на кадре 1080×1920, чтобы текст не упирался в края.
    return (f"FontName=Arial,FontSize={font_size},PrimaryColour={color},"
            f"OutlineColour={ass_color('#000000')},BackColour={ass_color('#000000')},"
            f"BorderStyle=1,Outline=3,Shadow=0,Alignment={alignment},MarginV={margin},"
            f"MarginL=25,MarginR=25")


def subtitle_filter(srt_path: Path, style: SubtitleStyle) -> str:
    """Готовый фрагмент фильтр-цепочки для ffmpeg.

    Путь экранируется: кириллица, пробелы и двоеточие диска ломали фильтр
    `subtitles` (п. 9 ТЗ), поэтому путь приводится к прямому виду и берётся
    в кавычки, а стиль идёт отдельным параметром.
    """
    return f"subtitles={escape_filter_path(srt_path)}:force_style='{force_style(style)}'"


def format_timestamp(seconds: float) -> str:
    """Секунды → «ЧЧ:ММ:СС,мс» (в .srt разделитель — запятая)."""
    total_ms = int(round(max(0.0, float(seconds)) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(segments: Iterable[tuple[float, float, str]]) -> str:
    """Собрать содержимое .srt из сегментов (начало, конец, текст)."""
    blocks: list[str] = []
    number = 0
    for start, end, text in segments:
        cleaned = " ".join(str(text).split())
        if not cleaned or end <= start:
            continue
        number += 1
        blocks.append(f"{number}\n{format_timestamp(start)} --> {format_timestamp(end)}\n"
                      f"{cleaned}\n")
    return "\n".join(blocks)


def write_srt(segments: Iterable[tuple[float, float, str]], path: Path) -> Path:
    """Записать .srt в UTF-8 (кириллица сохраняется без BOM)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_srt(segments), encoding="utf-8", newline="\n")
    log.info("Субтитры записаны: %s", path)
    return path


# --- Распознавание речи -------------------------------------------------------

def local_model_path(model_size: str = MODEL_SIZE) -> str:
    """Взять модель рядом с программой, если она там уже лежит.

    Так приложение работает офлайн и не качает ~480 МБ повторно: достаточно
    положить файлы модели в `models/faster-whisper-small` (или в `.hf-cache`).
    Ищем и рядом с модулями, и рядом с лаунчером/рабочей папкой — так модель
    находится и когда приложение запущено из одного файла-лаунчера.
    """
    if model_size != MODEL_SIZE:
        return model_size                   # указан свой путь или другая модель
    roots = [Path(__file__).resolve().parent]
    try:
        roots.append(Path(sys.argv[0]).resolve().parent)
    except (IndexError, OSError):
        pass
    roots.append(Path.cwd())

    seen: set[Path] = set()
    for root in roots:
        for candidate in (root / "models" / "faster-whisper-small",
                          root / ".hf-cache" / "faster-whisper-small"):
            if candidate in seen:
                continue
            seen.add(candidate)
            if (candidate / "model.bin").exists():
                log.info("Использую локальную модель: %s", candidate)
                return str(candidate)
    return model_size


def _load_model(model_size: str = MODEL_SIZE):
    """Загрузить модель один раз и переиспользовать её для всех клипов."""
    global _model, _model_size_loaded
    model_size = local_model_path(model_size)
    if _model is not None and _model_size_loaded == model_size:
        return _model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # пакет не установлен — субтитры отключены
        raise SubtitleUnavailable(INSTALL_HINT) from exc

    log.info("Загружаю модель faster-whisper %s (при первом запуске скачается)", model_size)
    try:
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
    except Exception as exc:
        raise SubtitleError(
            f"Не удалось загрузить модель faster-whisper «{model_size}»: {exc}\n\n"
            "Проверьте интернет при первом запуске и свободное место на диске."
        ) from exc
    _model_size_loaded = model_size
    return _model


def extract_audio(info: VideoInfo, start: float, duration: float, target: Path) -> Path:
    """Вырезать звук интервала в WAV 16 кГц моно — формат для распознавания."""
    command = [
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, duration):.3f}",
        "-i", str(info.path), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", "-y", str(target),
    ]
    log.info("Команда: %s", subprocess.list2cmdline(command))
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", creationflags=CREATE_NO_WINDOW)
    log.info("Код возврата: %s", result.returncode)
    if result.returncode != 0 or not target.exists():
        detail = (result.stderr or "").strip().splitlines()
        raise SubtitleError("не удалось выделить звук для распознавания: "
                            + (detail[-1] if detail else "ffmpeg вернул ошибку"))
    return target


def read_wav_mono(path: Path):
    """Прочитать WAV в массив float32 (−1…1) — формат, который ждёт модель.

    Звук разбирается стандартным модулем wave, а не библиотекой av: тогда версия
    av не влияет на распознавание (в av 19, например, убрали параметр
    `metadata_errors`, и faster-whisper 1.2.1 на нём падает).
    """
    try:
        import numpy
    except ImportError as exc:      # numpy приходит вместе с ctranslate2
        raise SubtitleError("для распознавания речи нужен numpy "
                            "(устанавливается вместе с faster-whisper)") from exc

    import wave
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        frames = handle.readframes(handle.getnframes())
    data = numpy.frombuffer(frames, dtype=numpy.int16).astype(numpy.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data


class SubtitleTask:
    """Готовит субтитры для клипов и отдаёт фильтр ffmpeg.

    Создаётся один раз перед нарезкой: модель загружается лениво и живёт до
    конца работы. Временные файлы (.wav и .srt) удаляются в cleanup().
    """

    def __init__(self, info: VideoInfo, work_dir: Path, style: SubtitleStyle,
                 model_size: str = MODEL_SIZE,
                 on_event: Callable[[str, dict], None] | None = None) -> None:
        self.info = info
        self.work_dir = Path(work_dir)
        self.style = style
        self.model_size = model_size
        self.on_event = on_event or (lambda kind, payload: None)
        self._created: list[Path] = []

    def _notify(self, kind: str, **payload) -> None:
        try:
            self.on_event(kind, payload)
        except Exception:  # события не должны ломать нарезку
            log.exception("Ошибка обработчика события субтитров")

    def filter_for(self, index: int, clip, on_progress: Callable[[float], None] | None = None,
                   should_cancel: Callable[[], bool] | None = None) -> str | None:
        """Распознать речь клипа и вернуть фильтр `subtitles` для ffmpeg."""
        if not is_available():
            raise SubtitleUnavailable(INSTALL_HINT)

        duration = max(0.1, clip.duration)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        wav = self.work_dir / f"clip_{index + 1:02d}.wav"
        srt = self.work_dir / f"clip_{index + 1:02d}.srt"
        self._created += [wav, srt]

        self._notify("subtitle_stage", index=index, stage="audio")
        extract_audio(self.info, clip.start, duration, wav)
        if should_cancel is not None and should_cancel():
            return None

        self._notify("subtitle_stage", index=index, stage="model")
        model = _load_model(self.model_size)
        self._notify("subtitle_stage", index=index, stage="speech")

        try:
            audio = read_wav_mono(wav)          # массив вместо пути: av не участвует
            segments, _info = model.transcribe(audio, beam_size=5, vad_filter=True)
        except SubtitleError:
            raise
        except Exception as exc:
            raise SubtitleError(f"распознавание речи не удалось: {exc}") from exc

        collected: list[tuple[float, float, str]] = []
        for segment in segments:
            if should_cancel is not None and should_cancel():
                return None
            collected.append((float(segment.start), float(segment.end), str(segment.text)))
            if on_progress is not None:
                on_progress(min(99.0, float(segment.end) / duration * 100.0))

        if not collected:
            self._notify("subtitle_empty", index=index)
            return None

        write_srt(collected, srt)
        if on_progress is not None:
            on_progress(100.0)
        self._notify("subtitle_ready", index=index, segments=len(collected),
                     path=str(srt))
        return subtitle_filter(srt, self.style)

    def cleanup(self) -> None:
        """Удалить временные .wav и .srt."""
        for path in self._created:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Не удалось удалить временный файл %s: %s", path, exc)
        self._created.clear()
        if self.work_dir.exists() and not any(self.work_dir.iterdir()):
            shutil.rmtree(self.work_dir, ignore_errors=True)
