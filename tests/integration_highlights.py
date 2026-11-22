# -*- coding: utf-8 -*-
"""Проверка поиска ярких моментов на настоящем звуке (этап 5, сверх ТЗ).

Запуск:  py -3 tests/integration_highlights.py

Собирается ролик из трёх частей: тихий фон (0–10 с), громкий участок с высокими
частотами (10–14 с, «смех/крик») и снова тихий фон (14–30 с). Алгоритм должен
указать именно на 10–14 с, а не на тишину. Проверяется и путь без numpy, и случай
видео без звуковой дорожки.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cutting import VideoInfo, ffmpeg_exe, probe_video  # noqa: E402
from highlights import (HighlightError, HighlightSettings,  # noqa: E402
                        find_highlights)
from intervals import format_timecode  # noqa: E402
from tests import remove_tree  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
WORK = HERE / "Видео тест" / "яркое"
VIDEO = WORK / "ролик с ярким моментом.mp4"
SILENT = WORK / "ролик без звука.mp4"
BURST = (10.0, 14.0)          # где находится «яркое» место
CHECKS: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    CHECKS.append(f"{'OK  ' if condition else 'FAIL'} {name} {extra}")


def make_video() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    if VIDEO.exists() and VIDEO.stat().st_size > 50_000:
        return
    audio = WORK / "звук.m4a"
    subprocess.run([
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=10",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=3000:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=16",
        "-filter_complex",
        "[0:a]volume=0.03[s1];"
        "[1:a]volume=0.35[s2a];[2:a]volume=0.30[s2b];"
        "[s2a][s2b]amix=inputs=2:normalize=0[s2];"
        "[3:a]volume=0.03[s3];"
        "[s1][s2][s3]concat=n=3:v=0:a=1[a]",
        "-map", "[a]", "-c:a", "aac", "-b:a", "128k", str(audio),
    ], check=True, capture_output=True)
    subprocess.run([
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x0A0A14:size=640x360:rate=30",
        "-i", str(audio), "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(VIDEO),
    ], check=True, capture_output=True)
    audio.unlink(missing_ok=True)
    subprocess.run([
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x0A0A14:size=640x360:rate=30",
        "-t", "5", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34",
        "-pix_fmt", "yuv420p", str(SILENT),
    ], check=True, capture_output=True)


def main() -> int:
    remove_tree(WORK)
    make_video()
    info = probe_video(VIDEO)
    print(f"ролик: {info.duration:.2f} с, звук: {info.audio_text}; "
          f"яркое место: {BURST[0]:.0f}–{BURST[1]:.0f} с")

    progress: list[float] = []
    settings = HighlightSettings(clip_length=6.0, max_clips=3, sensitivity=0.5)
    moments = find_highlights(info, settings, on_progress=progress.append)

    check("моменты найдены", bool(moments), f"найдено: {len(moments)}")
    check("прогресс анализа сообщался", len(progress) > 3 and max(progress) == 100.0,
          f"точек: {len(progress)}")
    if moments:
        print("\nнайденные моменты:")
        for moment in moments:
            print(f"  {format_timecode(moment.clip.start)}–{format_timecode(moment.clip.end)}"
                  f"  оценка {moment.score:.2f} — {moment.describe()}")
        best = max(moments, key=lambda item: item.score)
        check("лучший момент попадает в яркий участок",
              best.clip.start < BURST[1] and best.clip.end > BURST[0],
              f"{best.clip.start:.1f}–{best.clip.end:.1f} против {BURST}")
        check("длительность клипа как задано",
              all(abs(moment.clip.duration - 6.0) < 0.1 for moment in moments),
              str([round(moment.clip.duration, 1) for moment in moments]))
        check("клипы не пересекаются",
              all(first.clip.end <= second.clip.start
                  for first, second in zip(moments, moments[1:])))
        check("моменты внутри видео",
              all(0 <= moment.clip.start and moment.clip.end <= info.duration + 0.1
                  for moment in moments))
        check("в причинах упомянута громкость или всплеск",
              any("громче" in moment.describe() or "всплеск" in moment.describe()
                  for moment in moments),
              str([moment.describe() for moment in moments]))
        quiet_top = best.clip.start < 8.0 and best.clip.end <= BURST[0]
        check("лучший момент — не тишина в начале", not quiet_top,
              f"{best.clip.start:.1f}–{best.clip.end:.1f}")

    # --- видео без звуковой дорожки ------------------------------------------
    silent_info = probe_video(SILENT)
    try:
        find_highlights(silent_info, HighlightSettings(clip_length=2, max_clips=2))
        check("видео без звука даёт понятную ошибку", False, "ошибки не было")
    except HighlightError as exc:
        check("видео без звука даёт понятную ошибку", "звуковой дорожки" in str(exc),
              str(exc)[:60])

    # --- отмена посреди анализа (длинный звук, отмена по прогрессу) -----------
    long_audio = WORK / "длинный звук.wav"
    subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=1200",
                    "-c:a", "pcm_s16le", str(long_audio)], check=True, capture_output=True)
    long_info = VideoInfo(path=long_audio, duration=1200.0, width=0, height=0, fps=0.0,
                          has_audio=True)
    state = {"cancel": False}

    def cancel_soon(percent: float) -> None:
        if percent > 3.0:
            state["cancel"] = True

    from highlights import HighlightCancelled, analyze_audio
    try:
        analyze_audio(long_info, on_progress=cancel_soon,
                      should_cancel=lambda: state["cancel"])
        check("анализ прерывается по отмене", False, "отмена не сработала")
    except HighlightCancelled:
        check("анализ прерывается по отмене", True)
    except Exception as exc:                        # noqa: BLE001
        check("анализ прерывается по отмене", False, f"{type(exc).__name__}: {exc}")

    print()
    print("\n".join(CHECKS))
    failed = [line for line in CHECKS if line.startswith("FAIL")]
    print(f"\nИтого: {len(CHECKS) - len(failed)} из {len(CHECKS)} проверок пройдено")
    remove_tree(WORK)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
