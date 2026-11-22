# -*- coding: utf-8 -*-
"""Проверка настоящей нарезки на живом ffmpeg (этап 2 ТЗ).

Запуск:  py -3 tests/integration_cut.py
Скрипт создаёт короткий исходник в папке с кириллицей и пробелами, режет из него
вертикальные клипы и проверяет результат: 1080×1920, длительность ±1 с, звук есть,
отмена посреди работы и суффиксы при совпадении имён.

В общий прогон (python main.py --selftest) не входит: он кодирует видео и потому
занимает десятки секунд.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cutting import (ClipCutter, CutSettings, ffmpeg_exe,  # noqa: E402
                     probe_video)
from intervals import Clip, format_duration  # noqa: E402
from tests import remove_tree  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
WORK = HERE / "Видео тест"
SOURCE = WORK / "источник для нарезки 15 секунд.mp4"
OUTPUT = WORK / "Shorts"
CHECKS: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    CHECKS.append(f"{'OK  ' if condition else 'FAIL'} {name} {extra}")


def make_source() -> None:
    """Короткий ролик 640×360 со звуком — 15 секунд."""
    WORK.mkdir(parents=True, exist_ok=True)
    if SOURCE.exists() and SOURCE.stat().st_size > 50_000:
        return
    command = [
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", "15", "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k", str(SOURCE),
    ]
    subprocess.run(command, check=True, capture_output=True)
    print(f"исходник создан: {SOURCE.name} ({SOURCE.stat().st_size / 1e6:.1f} МБ)")


def collect_events(store: list, lock: threading.Lock):
    """Сборщик событий нарезки из рабочего потока."""
    def handler(kind: str, payload: dict) -> None:
        with lock:
            store.append((kind, payload))
    return handler


def run_cutter(clips: list, settings: CutSettings, cancel_after: float | None = None):
    """Запустить нарезку и вернуть (результаты, события)."""
    info = probe_video(SOURCE)
    events: list = []
    lock = threading.Lock()
    cutter = ClipCutter(info, clips, OUTPUT, settings, on_event=collect_events(events, lock))
    thread = threading.Thread(target=cutter.run, daemon=True)
    thread.start()
    if cancel_after is not None:
        time.sleep(cancel_after)
        cutter.cancel()
    thread.join(timeout=300)
    with lock:
        finished = [payload for kind, payload in events if kind == "finished"]
    results = finished[0]["results"] if finished else []
    return cutter, results, events


def main() -> int:
    remove_tree(OUTPUT)
    make_source()
    info = probe_video(SOURCE)
    print(f"исходник: {info.duration:.2f} с, {info.resolution_text}, {info.fps_text} fps, "
          f"звук: {info.audio_text}")

    # --- 1. Два коротких вертикальных клипа ---------------------------------
    clips = [Clip(2.0, 5.0), Clip(8.0, 10.0)]
    settings = CutSettings(vertical=True, crf=28)
    cutter, results, events = run_cutter(clips, settings)
    ready = [item for item in results if item.ok]
    check("нарезано два клипа", len(ready) == 2, f"{len(ready)} из {len(clips)}")

    for number, result in enumerate(ready):
        expected = clips[result.index].duration
        output = probe_video(result.path)
        check(f"клип {number + 1}: вертикаль 1080×1920",
              (output.width, output.height) == (1080, 1920), output.resolution_text)
        check(f"клип {number + 1}: длительность {expected:.0f} с ±1",
              abs(output.duration - expected) <= 1.0,
              f"{output.duration:.2f} с")
        check(f"клип {number + 1}: звук на месте", output.has_audio, output.audio_text)
        check(f"клип {number + 1}: имя по шаблону",
              result.path.name.endswith("_клип01.mp4") or result.path.name.endswith("_клип02.mp4"),
              result.path.name)

    progress = [payload["percent"] for kind, payload in events if kind == "clip_progress"]
    check("прогресс сообщался", bool(progress) and max(progress) > 50,
          f"точек: {len(progress)}, максимум {max(progress, default=0):.0f} %")

    # --- 2. Повторный запуск: не перезаписывать -----------------------------
    cutter, results, _ = run_cutter([Clip(2.0, 4.0)], settings)
    repeated = [item for item in results if item.ok]
    check("повторный запуск добавил суффикс _2",
          bool(repeated) and repeated[0].path.name.endswith("_2.mp4"),
          repeated[0].path.name if repeated else "нет файла")

    # --- 3. Отмена посреди нарезки ------------------------------------------
    before = {path.name for path in OUTPUT.glob("*.mp4")}
    cutter, results, events = run_cutter([Clip(0.0, 15.0)], settings, cancel_after=1.5)
    after = {path.name for path in OUTPUT.glob("*.mp4")}
    cancelled_events = [kind for kind, _ in events if kind == "cancelled"]
    check("нарезка отменена", cutter.cancelled and bool(cancelled_events),
          f"событий отмены: {len(cancelled_events)}")
    check("частичный файл удалён", before == after, f"новые файлы: {sorted(after - before)}")
    check("отменённый клип не попал в результаты",
          all(not item.ok for item in results if item.index == 0) if results else True)

    # --- 4. Горизонтальный режим «оставить как есть» ------------------------
    cutter, results, _ = run_cutter([Clip(1.0, 3.0)], CutSettings(vertical=False, crf=30))
    kept = [item for item in results if item.ok]
    if kept:
        output = probe_video(kept[0].path)
        check("режим «как есть» сохранил размеры",
              (output.width, output.height) == (640, 360), output.resolution_text)
    else:
        check("режим «как есть» отработал", False, "клип не создан")

    # --- итог ---------------------------------------------------------------
    print()
    print("\n".join(CHECKS))
    failed = [line for line in CHECKS if line.startswith("FAIL")]
    print(f"\nИтого: {len(CHECKS) - len(failed)}/{len(CHECKS)} проверок пройдено")
    if not failed:
        print("Готовые файлы:")
        for path in sorted(OUTPUT.glob("*.mp4")):
            print(f"  {path.name} — {format_duration(probe_video(path).duration)}")
    remove_tree(OUTPUT)
    SOURCE.unlink(missing_ok=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
