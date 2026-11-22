# -*- coding: utf-8 -*-
"""Работа с ffmpeg: параметры видео и нарезка клипов.

ffmpeg берётся из пакета imageio-ffmpeg (п. 2 ТЗ), поэтому отдельно
устанавливать его в систему не нужно. Если пакет не установлен, попытка
получить бинарник даёт понятную ошибку FfmpegNotFound, которую интерфейс
показывает пользователю по-русски.

Нарезка (п. 3.4 ТЗ): точные границы через `-ss` и `-t` перед `-i` с
перекодированием — копирование потока (`-c copy`) не используется, иначе
границы сдвинулись бы по ключевым кадрам. Кроп в 9:16 делается ДО масштаба,
чтобы 4K-источники не тормозили и не съедали память.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

__all__ = [
    "FfmpegNotFound", "ProbeError", "CutError", "VideoInfo", "CutSettings", "ClipResult",
    "ffmpeg_exe", "ffprobe_exe", "probe_video", "probe_duration", "crop_scale_filter",
    "build_cut_command", "clip_output_name", "unique_output_path", "ClipCutter",
    "escape_filter_path",
]

log = logging.getLogger("shorts")

# Не показывать консольное окно при запуске ffmpeg из GUI (только Windows).
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
RUN_TIMEOUT = 120
CANCEL_GRACE_SECONDS = 3.0

_TIME_RE = re.compile(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


class FfmpegNotFound(RuntimeError):
    """Бинарник ffmpeg не найден."""


class ProbeError(RuntimeError):
    """Не удалось прочитать параметры видеофайла."""


class CutError(RuntimeError):
    """Нарезка клипа не удалась."""


class CutCancelled(RuntimeError):
    """Нарезку прервал пользователь."""


@dataclass
class VideoInfo:
    """Сведения об исходном видео."""

    path: Path
    duration: float          # секунды
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: str = ""

    @property
    def resolution_text(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return "неизвестно"

    @property
    def fps_text(self) -> str:
        if self.fps and self.fps > 0:
            return f"{self.fps:.2f}".rstrip("0").rstrip(".")
        return "неизвестно"

    @property
    def audio_text(self) -> str:
        return "есть" if self.has_audio else "нет"


# --- Поиск бинарников ---------------------------------------------------------

_ffmpeg_cache: str | None = None
_ffprobe_cache: str | None = None
_ffprobe_checked = False


def ffmpeg_exe() -> str:
    """Путь к ffmpeg из imageio-ffmpeg (или из PATH, если пакет не установлен)."""
    global _ffmpeg_cache
    if _ffmpeg_cache:
        return _ffmpeg_cache

    try:
        import imageio_ffmpeg  # импорт внутри функции: без пакета приложение работает
    except ImportError:
        found = shutil.which("ffmpeg")
        if found:
            _ffmpeg_cache = found
            return found
        raise FfmpegNotFound(
            "Не установлен пакет imageio-ffmpeg, который содержит ffmpeg.\n\n"
            "Выполните в командной строке:\n    pip install imageio-ffmpeg\n"
            "или запустите ЗАПУСК.bat"
        ) from None

    try:
        _ffmpeg_cache = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # пакет есть, но бинарник не скачался
        raise FfmpegNotFound(f"Не удалось получить ffmpeg из пакета imageio-ffmpeg: {exc}") from exc
    log.info("ffmpeg: %s", _ffmpeg_cache)
    return _ffmpeg_cache


def ffprobe_exe() -> str | None:
    """Путь к ffprobe, если он есть рядом с ffmpeg или в PATH (иначе None)."""
    global _ffprobe_cache, _ffprobe_checked
    if _ffprobe_checked:
        return _ffprobe_cache

    _ffprobe_checked = True
    candidates: list[Path] = []
    try:
        candidates.append(Path(ffmpeg_exe()).with_name("ffprobe.exe"))
        candidates.append(Path(ffmpeg_exe()).with_name("ffprobe"))
    except FfmpegNotFound:
        pass

    system = shutil.which("ffprobe")
    if system:
        candidates.append(Path(system))

    for candidate in candidates:
        if candidate.is_file():
            _ffprobe_cache = str(candidate)
            log.info("ffprobe: %s", _ffprobe_cache)
            return _ffprobe_cache

    log.info("ffprobe не найден — параметры читаю разбором вывода ffmpeg -i")
    return None


def _run(command: list[str], timeout: int = RUN_TIMEOUT) -> subprocess.CompletedProcess:
    """Запустить команду и вернуть результат; кодировка — UTF-8 с заменами."""
    log.info("Команда: %s", subprocess.list2cmdline(command))
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=CREATE_NO_WINDOW, timeout=timeout)
    except FileNotFoundError as exc:
        raise FfmpegNotFound(f"Не найден исполняемый файл: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"Команда не ответила за {timeout} секунд.") from exc
    log.info("Код возврата: %s", result.returncode)
    if result.stderr:
        log.debug("stderr: %s", result.stderr.strip())
    return result


# --- Чтение параметров видео --------------------------------------------------

def probe_video(path: str | Path) -> VideoInfo:
    """Прочитать длительность, разрешение, fps и наличие звука.

    Сначала пробуем ffprobe (быстро, JSON), затем — разбор вывода `ffmpeg -i`
    (разрешён п. 3.1 ТЗ, нужен, когда ffprobe нет).
    """
    video_path = Path(path)
    if not video_path.is_file():
        raise ProbeError(f"Файл не найден: {video_path}")
    if video_path.stat().st_size == 0:
        raise ProbeError(f"Файл пустой: {video_path.name}")

    errors: list[str] = []
    probe = ffprobe_exe()
    if probe:
        try:
            return _probe_with_ffprobe(probe, video_path)
        except ProbeError as exc:
            errors.append(str(exc))
            log.warning("ffprobe не справился: %s", exc)
    else:
        errors.append("ffprobe не найден")

    try:
        return _probe_with_ffmpeg(video_path)
    except ProbeError as exc:
        errors.append(str(exc))
        raise ProbeError("ffmpeg не смог открыть файл: " + " / ".join(errors)) from exc


def _probe_with_ffprobe(probe: str, video_path: Path) -> VideoInfo:
    """Разбор JSON-вывода ffprobe."""
    result = _run([probe, "-v", "error", "-hide_banner",
                   "-print_format", "json", "-show_format", "-show_streams",
                   str(video_path)])
    if result.returncode != 0:
        raise ProbeError(_last_line(result.stderr) or "ffprobe вернул ошибку")

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"не удалось разобрать ответ ffprobe ({exc})") from exc

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("в файле не найден видеопоток")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = _to_float(fmt.get("duration")) or _to_float(video.get("duration")) or 0.0
    fps = _parse_fraction(video.get("avg_frame_rate")) or _parse_fraction(video.get("r_frame_rate")) or 0.0
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)

    if duration <= 0:
        raise ProbeError("ffprobe не сообщил длительность")

    return VideoInfo(path=video_path, duration=duration, width=width, height=height,
                     fps=fps, has_audio=audio is not None,
                     video_codec=str(video.get("codec_name") or ""))


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_SIZE_RE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"([\d.]+)\s*fps")


def _probe_with_ffmpeg(video_path: Path) -> VideoInfo:
    """Разбор вывода `ffmpeg -i файл` (без выходного файла ffmpeg всегда даёт код 1)."""
    result = _run([ffmpeg_exe(), "-hide_banner", "-i", str(video_path)])
    text = (result.stderr or "") + (result.stdout or "")

    match = _DURATION_RE.search(text)
    if not match:
        raise ProbeError(_last_line(text) or "ffmpeg не сообщил длительность")
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    duration = hours * 3600 + minutes * 60 + seconds

    size = _SIZE_RE.search(text)
    fps_match = _FPS_RE.search(text)
    codec_match = re.search(r"Video:\s*([A-Za-z0-9_\-]+)", text)

    video_codec = codec_match.group(1) if codec_match else ""
    return VideoInfo(
        path=video_path,
        duration=duration,
        width=int(size.group(1)) if size else 0,
        height=int(size.group(2)) if size else 0,
        fps=float(fps_match.group(1)) if fps_match else 0.0,
        has_audio="Audio:" in text,
        video_codec="" if video_codec in {"", "Stream"} else video_codec,
    )


def _to_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _parse_fraction(value: object) -> float:
    """«30000/1001» -> 29.97."""
    if not value:
        return 0.0
    text = str(value)
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        denominator_value = _to_float(denominator)
        if denominator_value == 0:
            return 0.0
        return _to_float(numerator) / denominator_value
    return _to_float(text)


def _last_line(text: str) -> str:
    """Последняя непустая строка вывода — короткое человеческое сообщение."""
    for line in reversed((text or "").strip().splitlines()):
        if line.strip():
            return line.strip()
    return ""


# --- Параметры нарезки и команда ffmpeg ---------------------------------------

@dataclass
class CutSettings:
    """Настройки экспорта (п. 3.3 ТЗ)."""

    vertical: bool = True           # «Вертикаль 9:16» или «Оставить как есть»
    target_width: int = 1080
    target_height: int = 1920
    crf: int = 20                   # 16–28, меньше — лучше качество
    preset: str = "veryfast"
    audio_bitrate: str = "128k"

    def clamped_crf(self) -> int:
        return max(16, min(28, int(self.crf)))


def crop_scale_filter(info: VideoInfo, settings: CutSettings) -> str | None:
    """Фильтр кропа и масштаба. Кроп считается в питоне и идёт ДО scale.

    Для вертикали берём центральную часть кадра с соотношением 9:16 и только
    потом растягиваем до 1080×1920 — так 4K-источник обрабатывается быстрее.
    """
    if not info.width or not info.height:
        return None

    if not settings.vertical:
        # Оставляем как есть, но гарантируем чётные стороны под yuv420p.
        if info.width % 2 == 0 and info.height % 2 == 0:
            return None
        return "scale=trunc(iw/2)*2:trunc(ih/2)*2"

    target_ratio = settings.target_width / settings.target_height
    if info.width / info.height > target_ratio:
        crop_height = info.height
        crop_width = int(round(info.height * target_ratio))
    else:
        crop_width = info.width
        crop_height = int(round(info.width / target_ratio))

    crop_width -= crop_width % 2          # чётные стороны обязательны для H.264
    crop_height -= crop_height % 2
    x = max(0, (info.width - crop_width) // 2)
    y = max(0, (info.height - crop_height) // 2)
    return (f"crop={crop_width}:{crop_height}:{x}:{y},"
            f"scale={settings.target_width}:{settings.target_height}:flags=lanczos")


def build_cut_command(info: VideoInfo, start: float, duration: float, output: Path,
                      settings: CutSettings, subtitles_filter: str | None = None) -> list[str]:
    """Собрать команду ffmpeg для одного клипа.

    `-ss` и `-t` стоят перед `-i` — как требует п. 3.4 ТЗ. Потоки перекодируются
    (libx264 + AAC), поэтому границы точные, а не по ключевым кадрам.
    """
    command = [
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "info",
        "-ss", f"{max(0.0, start):.3f}",
        "-t", f"{max(0.1, duration):.3f}",
        "-i", str(info.path),
        "-map", "0:v:0",
    ]
    if info.has_audio:
        command += ["-map", "0:a:0?"]

    filters = []
    geometry = crop_scale_filter(info, settings)
    if geometry:
        filters.append(geometry)
    if subtitles_filter:
        filters.append(subtitles_filter)
    if filters:
        command += ["-vf", ",".join(filters)]

    command += [
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.clamped_crf()),
        "-pix_fmt", "yuv420p",
    ]
    if info.has_audio:
        command += ["-c:a", "aac", "-b:a", settings.audio_bitrate, "-ac", "2"]
    command += ["-movflags", "+faststart", "-y", str(output)]
    return command


def escape_filter_path(path: Path) -> str:
    """Экранирование пути для фильтра ffmpeg (кириллица, пробелы, двоеточия).

    В фильтре `subtitles` обратные слэши и двоеточия разделяют параметры,
    поэтому путь приводится к прямому виду и экранируется, а сам фильтр
    заключается в кавычки.
    """
    text = str(path).replace("\\", "/")
    text = text.replace(":", r"\:")
    text = text.replace("'", r"\'")
    return f"'{text}'"


# --- Имена выходных файлов ----------------------------------------------------

def clip_output_name(stem: str, index: int) -> str:
    """`<имя исходника>_клип01.mp4` (нумерация с единицы, как в ТЗ)."""
    return f"{stem}_клип{index + 1:02d}.mp4"


def unique_output_path(folder: Path, stem: str, index: int) -> Path:
    """Не перезаписывать существующие файлы: добавлять суффикс `_2`, `_3`…"""
    candidate = folder / clip_output_name(stem, index)
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem}_клип{index + 1:02d}_{counter}.mp4"
        counter += 1
    return candidate


def probe_duration(path: str | Path) -> float:
    """Длительность готового файла — для отчёта и проверок."""
    try:
        return probe_video(path).duration
    except (ProbeError, FfmpegNotFound):
        return 0.0


# --- Нарезка ------------------------------------------------------------------

@dataclass
class ClipResult:
    """Результат по одному клипу."""

    index: int
    path: Path | None
    ok: bool
    duration: float = 0.0
    message: str = ""


@dataclass
class _ProgressState:
    """Разделяемое состояние для разбора вывода ffmpeg."""

    duration: float = 0.0
    percent: float = 0.0
    last_message: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


class ClipCutter:
    """Последовательная нарезка клипов с прогрессом и отменой.

    Работает в отдельном потоке: интерфейс получает события через on_event,
    а отмена выполняется методом cancel() — он убивает текущий процесс ffmpeg.
    """

    def __init__(self, info: VideoInfo, clips: list, output_dir: Path,
                 settings: CutSettings, on_event: Callable[[str, dict], None],
                 subtitles_for: Callable[[int], Path | None] | None = None) -> None:
        self.info = info
        self.clips = clips
        self.output_dir = Path(output_dir)
        self.settings = settings
        self.on_event = on_event
        self.subtitles_for = subtitles_for
        self._process: subprocess.Popen | None = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    # -- управление ---------------------------------------------------------
    def cancel(self) -> None:
        """Прервать нарезку: убить текущий ffmpeg. Частичный файл удаляется."""
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            log.info("Отмена: завершаю ffmpeg (pid %s)", process.pid)
            try:
                process.terminate()
                process.wait(timeout=CANCEL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
            except OSError as exc:
                log.warning("Не удалось завершить ffmpeg: %s", exc)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # -- основной цикл ------------------------------------------------------
    def run(self) -> list[ClipResult]:
        results: list[ClipResult] = []
        self.output_dir.mkdir(parents=True, exist_ok=True)
        total = len(self.clips)
        self.on_event("started", {"total": total, "output_dir": str(self.output_dir)})

        for index, clip in enumerate(self.clips):
            if self._cancelled.is_set():
                break
            output = unique_output_path(self.output_dir, self.info.path.stem, index)
            self.on_event("clip_start", {
                "index": index, "total": total, "path": str(output),
                "name": output.name, "duration": clip.duration,
            })
            try:
                result = self._cut_one(index, clip, output)
            except CutCancelled:
                log.info("Нарезка прервана на клипе №%d", index + 1)
                self.on_event("cancelled", {"index": index, "path": str(output)})
                results.append(ClipResult(index=index, path=None, ok=False,
                                          message="Отменено пользователем"))
                break
            except CutError as exc:
                log.error("Клип №%d не нарезан: %s", index + 1, exc)
                self.on_event("clip_error", {"index": index, "message": str(exc)})
                results.append(ClipResult(index=index, path=None, ok=False, message=str(exc)))
                continue
            except (FfmpegNotFound, ProbeError) as exc:
                # Без ffmpeg продолжать нечем — сообщаем и выходим, но обязательно
                # отправляем "finished", иначе интерфейс останется «занятым».
                log.error("Остановка нарезки: %s", exc)
                self.on_event("clip_error", {"index": index, "message": str(exc)})
                results.append(ClipResult(index=index, path=None, ok=False, message=str(exc)))
                for rest in range(index + 1, len(self.clips)):
                    results.append(ClipResult(index=rest, path=None, ok=False,
                                              message="не обработан: ffmpeg недоступен"))
                break
            except Exception as exc:                    # noqa: BLE001 — не роняем поток
                log.exception("Неожиданная ошибка на клипе №%d", index + 1)
                message = f"неожиданная ошибка: {exc}"
                self.on_event("clip_error", {"index": index, "message": message})
                results.append(ClipResult(index=index, path=None, ok=False, message=message))
                continue

            results.append(result)
            self.on_event("clip_done", {
                "index": index, "path": str(result.path), "name": result.path.name,
                "duration": result.duration,
            })

        if self._cancelled.is_set():
            self.on_event("finished", {"results": results, "cancelled": True})
        else:
            self.on_event("finished", {"results": results, "cancelled": False})
        return results

    def _prepare_subtitles(self, index: int, clip) -> str | None:
        """Получить фильтр субтитров для клипа (распознавание речи — этап 3).

        Провайдер делает долгую работу (вырезание звука и распознавание), поэтому
        вызывается здесь, в рабочем потоке. Его ошибки превращаются в ошибку
        конкретного клипа: остальные клипы продолжают нарезаться.
        """
        if self.subtitles_for is None:
            return None
        self.on_event("subtitle_start", {"index": index, "total": len(self.clips)})
        try:
            result = self.subtitles_for(
                index, clip,
                lambda percent: self.on_event("subtitle_progress",
                                              {"index": index, "percent": percent}),
                lambda: self._cancelled.is_set())
        except CutCancelled:
            raise
        except Exception as exc:            # noqa: BLE001 — сообщение уйдёт в интерфейс
            raise CutError(str(exc)) from exc
        if self._cancelled.is_set():
            raise CutCancelled()
        return result

    def _cut_one(self, index: int, clip, output: Path) -> ClipResult:
        """Нарезать один клип и следить за прогрессом."""
        subtitle_filter = self._prepare_subtitles(index, clip)
        command = build_cut_command(self.info, clip.start, clip.duration, output,
                                    self.settings, subtitle_filter)
        state = _ProgressState(duration=max(0.1, clip.duration))

        try:
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW)
        except FileNotFoundError as exc:
            raise FfmpegNotFound(f"Не найден исполняемый файл: {command[0]}") from exc

        with self._lock:
            self._process = process
        log.info("Команда: %s", subprocess.list2cmdline(command))

        assert process.stderr is not None
        reader = threading.Thread(target=self._read_progress,
                                  args=(process.stderr, index, state), daemon=True)
        reader.start()
        return_code = process.wait()
        reader.join(timeout=5)
        with self._lock:
            self._process = None
        log.info("Код возврата: %s", return_code)

        if self._cancelled.is_set():
            _remove_partial(output)
            raise CutCancelled()
        if return_code != 0:
            message = state.last_message or f"ffmpeg завершился с кодом {return_code}"
            _remove_partial(output)
            raise CutError(_humanize_ffmpeg_error(message))
        if not output.exists() or output.stat().st_size == 0:
            raise CutError("ffmpeg не создал файл — возможно, не хватило места на диске")

        self.on_event("clip_progress", {"index": index, "percent": 100.0})
        return ClipResult(index=index, path=output, ok=True,
                          duration=probe_duration(output) or clip.duration)

    def _read_progress(self, stream, index: int, state: _ProgressState) -> None:
        """Разбирать вывод ffmpeg: строка `time=00:00:12.34` даёт проценты."""
        for line in stream:
            match = _TIME_RE.search(line)
            if match:
                hours, minutes = int(match.group(1)), int(match.group(2))
                seconds = float(match.group(3))
                elapsed = hours * 3600 + minutes * 60 + seconds
                percent = min(99.9, elapsed / state.duration * 100.0)
                if percent - state.percent >= 0.5:
                    state.percent = percent
                    self.on_event("clip_progress", {"index": index, "percent": percent})
                continue
            stripped = line.strip()
            if stripped and not stripped.startswith(("frame=", "size=", "bitrate=",
                                                      "speed=", "video:", "Stream",
                                                      "Input", "Output", "Duration",
                                                      "Metadata", "  ", "Press")):
                state.last_message = stripped


def _remove_partial(path: Path) -> None:
    """Удалить недописанный файл, чтобы не оставалось «битых» клипов."""
    try:
        if path.exists():
            path.unlink()
            log.info("Удалён частичный файл: %s", path)
    except OSError as exc:
        log.warning("Не удалось удалить частичный файл %s: %s", path, exc)


def _humanize_ffmpeg_error(message: str) -> str:
    """Превратить строку ffmpeg в понятное объяснение (п. 3.7 ТЗ)."""
    text = message.strip()
    lowered = text.lower()
    if "no such file" in lowered or "не удается найти" in lowered:
        return "ffmpeg не смог открыть файл: файл не найден или недоступен."
    if "invalid data found" in lowered or "moov atom not found" in lowered:
        return "ffmpeg не смог открыть файл: файл повреждён или это не видео."
    if "no space left" in lowered:
        return "На диске закончилось место — освободите место и повторите."
    if "permission denied" in lowered:
        return "Нет доступа к папке вывода — выберите другую папку."
    return f"ffmpeg не смог обработать клип: {text}"
