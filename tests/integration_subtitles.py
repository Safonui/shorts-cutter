# -*- coding: utf-8 -*-
"""Проверка вшивания субтитров на живом ffmpeg (этап 3 ТЗ).

Запуск:  py -3 tests/integration_subtitles.py

faster-whisper здесь не нужен: .srt пишется вручную (с русским текстом и в папке
с кириллицей и пробелами), а проверяется именно вшивание — что текст появился,
не обрезан по краям, стоит внизу или по центру, меняет цвет и размер.

Кадры читаются как rawvideo и разбираются стандартной библиотекой, поэтому
Pillow не требуется.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cutting import ClipCutter, CutSettings, ffmpeg_exe, probe_video  # noqa: E402
from intervals import Clip  # noqa: E402
from subtitles import SubtitleStyle, subtitle_filter, write_srt  # noqa: E402
from tests import remove_tree  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
WORK = HERE / "Видео тест"
SOURCE = WORK / "источник для субтитров 12 секунд.mp4"
SRT = WORK / "суб титры для проверки.srt"
OUTPUT = WORK / "Shorts"
CHECKS: list[str] = []

SEGMENTS = [(1.0, 2.5, "Привет, это проверка субтитров"),
            (4.0, 5.0, "Вторая строка текста")]
CLIP = Clip(0.5, 6.5)
TEXT_TIMES = (1.5, 4.5)       # внутри реплик — текст должен быть виден
EMPTY_TIMES = (3.2, 5.7)      # между репликами и после последней — текста нет


def check(name: str, condition: bool, extra: str = "") -> None:
    CHECKS.append(f"{'OK  ' if condition else 'FAIL'} {name} {extra}")


def make_source() -> None:
    """Тёмный однотонный ролик: любые яркие пиксели в кадре — это субтитры."""
    WORK.mkdir(parents=True, exist_ok=True)
    if SOURCE.exists() and SOURCE.stat().st_size > 20_000:
        return
    subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=0x0A0A14:size=640x360:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "12", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(SOURCE)],
                   check=True, capture_output=True)
    print(f"исходник создан: {SOURCE.name} (тёмный фон)")


def extract_frame(path: Path, seconds: float, width: int, height: int) -> bytes:
    """Кадр из готового клипа в формате RGB24."""
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}",
         "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    frame = result.stdout
    expected = width * height * 3
    if len(frame) < expected:
        raise RuntimeError(f"кадр не получен: {len(frame)} байт вместо {expected}")
    return frame[:expected]


def find_text(frame: bytes, width: int, height: int, mode: str = "white") -> dict:
    """Найти пиксели текста и вернуть их количество и рамку."""
    count = 0
    x_min, y_min, x_max, y_max = width, height, -1, -1
    step = 3
    for y in range(0, height, 1):
        row = frame[y * width * 3:(y + 1) * width * 3]
        for x in range(0, width, step):
            index = x * 3
            red, green, blue = row[index], row[index + 1], row[index + 2]
            if mode == "white":
                hit = min(red, green, blue) > 200
            else:   # жёлтый: красный и зелёный высокие, синий низкий
                hit = red > 190 and green > 190 and blue < 130
            if hit:
                count += 1
                x_min, y_min = min(x_min, x), min(y_min, y)
                x_max, y_max = max(x_max, x), max(y_max, y)
    if count == 0:
        return {"count": 0, "height_px": 0}
    return {"count": count, "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
            "height_px": y_max - y_min + 1, "center_y": (y_min + y_max) / 2}


def cut_with_subtitles(style: SubtitleStyle, suffix: str) -> Path:
    """Нарезать клип с вшитыми субтитрами и вернуть путь к файлу."""
    info = probe_video(SOURCE)
    task_filter = subtitle_filter(SRT, style)
    results: list = []

    def provider(index, clip, on_progress=None, should_cancel=None):
        return task_filter

    cutter = ClipCutter(info, [CLIP], OUTPUT, CutSettings(vertical=True, crf=28),
                        on_event=lambda kind, payload: None, subtitles_for=provider)
    results = cutter.run()
    ready = [item for item in results if item.ok]
    if not ready:
        raise RuntimeError("клип не создан")
    target = ready[0].path
    renamed = target.with_name(f"{suffix}_{target.name}")
    target.replace(renamed)
    return renamed


def main() -> int:
    remove_tree(OUTPUT)
    make_source()
    write_srt(SEGMENTS, SRT)
    print(f"субтитры записаны: {SRT}")

    # --- 1. Белый текст внизу, размер по умолчанию ---------------------------
    bottom = cut_with_subtitles(SubtitleStyle(font_size=48, position="низ", color="белый"),
                                "низ")
    output = probe_video(bottom)
    check("клип с субтитрами создан", (output.width, output.height) == (1080, 1920),
          output.resolution_text)
    check("звук на месте", output.has_audio, output.audio_text)

    text = find_text(extract_frame(bottom, TEXT_TIMES[0], 1080, 1920), 1080, 1920, "white")
    second = find_text(extract_frame(bottom, TEXT_TIMES[1], 1080, 1920), 1080, 1920, "white")
    check("текст виден в первой реплике", text["count"] > 200, f"пикселей: {text['count']}")
    check("текст виден во второй реплике", second["count"] > 200,
          f"пикселей: {second['count']}")
    if text["count"]:
        print(f"    рамка текста: x {text['x_min']}–{text['x_max']}, "
              f"y {text['y_min']}–{text['y_max']}, высота {text['height_px']} px")
        check("текст не обрезан по бокам",
              text["x_min"] > 10 and text["x_max"] < 1070,
              f"x {text['x_min']}–{text['x_max']}")
        check("текст не обрезан снизу", text["y_max"] < 1900, f"y_max {text['y_max']}")
        check("текст в нижней части кадра", text["center_y"] > 1920 * 0.6,
              f"центр по y {text['center_y']:.0f}")
        check("высота строки сопоставима с настройкой 48 px",
              20 <= text["height_px"] <= 120, f"{text['height_px']} px")

    for moment in EMPTY_TIMES:
        empty = find_text(extract_frame(bottom, moment, 1080, 1920), 1080, 1920, "white")
        check(f"в момент {moment} с текста нет", empty["count"] < 60,
              f"пикселей: {empty['count']}")

    # --- 2. Жёлтый цвет и положение «центр» ---------------------------------
    yellow = cut_with_subtitles(SubtitleStyle(font_size=48, position="центр",
                                              color="жёлтый"), "жёлтый")
    frame = extract_frame(yellow, TEXT_TIMES[0], 1080, 1920)
    yellow_text = find_text(frame, 1080, 1920, "yellow")
    white_text = find_text(frame, 1080, 1920, "white")
    check("жёлтый текст найден", yellow_text["count"] > 200,
          f"пикселей: {yellow_text['count']}, белых: {white_text['count']}")
    if yellow_text["count"]:
        check("«центр» ставит текст около середины кадра",
              abs(yellow_text["center_y"] - 1920 / 2) < 300,
              f"центр по y {yellow_text['center_y']:.0f}")
        check("«центр» поднимает текст выше, чем «низ»",
              yellow_text["center_y"] < text["center_y"],
              f"{yellow_text['center_y']:.0f} против {text['center_y']:.0f}")

    # --- 3. Размер шрифта 96 -------------------------------------------------
    big = cut_with_subtitles(SubtitleStyle(font_size=96, position="низ", color="белый"),
                             "крупный")
    big_text = find_text(extract_frame(big, 1.8, 1080, 1920), 1080, 1920, "white")
    check("крупный шрифт заметно выше",
          big_text["count"] > text["count"] and big_text.get("height_px", 0) > text["height_px"],
          f"высота {big_text.get('height_px', 0)} px против {text['height_px']} px")
    if big_text["count"]:
        check("крупный текст тоже без обрезки по краям",
              big_text["x_min"] > 10 and big_text["x_max"] < 1070,
              f"x {big_text['x_min']}–{big_text['x_max']}")

    print()
    print("\n".join(CHECKS))
    failed = [line for line in CHECKS if line.startswith("FAIL")]
    print(f"\nИтого: {len(CHECKS) - len(failed)}/{len(CHECKS)} проверок пройдено")
    remove_tree(OUTPUT)
    SOURCE.unlink(missing_ok=True)
    SRT.unlink(missing_ok=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
