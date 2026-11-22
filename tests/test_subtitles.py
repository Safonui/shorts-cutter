# -*- coding: utf-8 -*-
"""Тесты субтитров: таймкоды, .srt, оформление и экранирование пути.

faster-whisper не нужен: проверяются части, отвечающие за текст и фильтр ffmpeg.
Исключение — тест отключённого модуля: он выполняется только там, где пакета нет.
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import subtitles  # noqa: E402
from cutting import VideoInfo  # noqa: E402
from subtitles import (SubtitleStyle, SubtitleTask, SubtitleUnavailable,  # noqa: E402
                       ass_color, build_srt, estimate_seconds, force_style,
                       format_timestamp, is_available, read_wav_mono,
                       subtitle_filter, unavailable_reason, write_srt)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_temp_folder(name: str) -> Path:
    """Временная папка внутри проекта (системный %TEMP% может быть недоступен)."""
    folder = PROJECT_ROOT / f"_tmp_sub_{name}"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class TestTimestamps(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_timestamp(0), "00:00:00,000")
        self.assertEqual(format_timestamp(1.5), "00:00:01,500")
        self.assertEqual(format_timestamp(61.25), "00:01:01,250")
        self.assertEqual(format_timestamp(3725.1234), "01:02:05,123")

    def test_negative_is_zero(self):
        self.assertEqual(format_timestamp(-5), "00:00:00,000")

    def test_srt_uses_comma(self):
        self.assertIn(",", format_timestamp(2.0))
        self.assertNotIn(".", format_timestamp(2.0))


class TestSrtContent(unittest.TestCase):
    def test_blocks_and_numbering(self):
        text = build_srt([(0.0, 1.5, "Привет"), (1.5, 3.0, "как дела")])
        self.assertEqual(text,
                         "1\n00:00:00,000 --> 00:00:01,500\nПривет\n\n"
                         "2\n00:00:01,500 --> 00:00:03,000\nкак дела\n")

    def test_empty_text_skipped(self):
        text = build_srt([(0.0, 1.0, "  "), (1.0, 2.0, "есть"), (2.0, 2.0, "нулевой")])
        self.assertIn("есть", text)
        self.assertNotIn("нулевой", text)
        self.assertEqual(text.count("-->"), 1)   # нумерация идёт по факту, без дыр

    def test_whitespace_collapsed(self):
        text = build_srt([(0.0, 1.0, "  две   строки \n вместе ")])
        self.assertIn("две строки вместе", text)

    def test_write_and_read_utf8(self):
        folder = make_temp_folder("srt")
        path = folder / "клип 01.srt"
        try:
            write_srt([(0.0, 1.0, "Привет, мир")], path)
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))     # без BOM
            self.assertEqual(path.read_text(encoding="utf-8").count("Привет, мир"), 1)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class TestStyle(unittest.TestCase):
    def test_ass_color_is_bgr(self):
        self.assertEqual(ass_color("#FFFFFF"), "&H00FFFFFF")
        self.assertEqual(ass_color("#000000"), "&H00000000")
        self.assertEqual(ass_color("#FF0000"), "&H000000FF")   # красный
        self.assertEqual(ass_color("#FFFF00"), "&H0000FFFF")   # жёлтый

    def test_font_size_scaled_to_output(self):
        # 48 px на кадре 1080×1920 — это 7 в системе libass 384×288.
        style = force_style(SubtitleStyle(font_size=48))
        self.assertIn("FontSize=7,", style)
        self.assertIn("FontSize=14,", force_style(SubtitleStyle(font_size=96)))

    def test_outline_always_black(self):
        for color in ("белый", "жёлтый"):
            style = force_style(SubtitleStyle(color=color))
            self.assertIn("OutlineColour=&H00000000", style)
            self.assertIn("Outline=3", style)
            self.assertIn("BorderStyle=1", style)

    def test_position(self):
        bottom = force_style(SubtitleStyle(position="низ"))
        center = force_style(SubtitleStyle(position="центр"))
        self.assertIn("Alignment=2", bottom)      # низ по центру
        self.assertIn("MarginV=22", bottom)       # ≈150 px от низа на кадре 1080×1920
        self.assertIn("Alignment=10", center)     # середина по центру (нумерация ASS v4)
        self.assertIn("MarginV=0", center)

    def test_color_selection(self):
        self.assertIn("PrimaryColour=&H00FFFFFF", force_style(SubtitleStyle(color="белый")))
        self.assertIn("PrimaryColour=&H0000FFFF", force_style(SubtitleStyle(color="жёлтый")))

    def test_unknown_values_fall_back(self):
        style = force_style(SubtitleStyle(color="зелёный", position="сверху"))
        self.assertIn("PrimaryColour=&H00FFFFFF", style)   # белый по умолчанию
        self.assertIn("Alignment=2", style)                # низ по умолчанию


class TestFilter(unittest.TestCase):
    def test_path_escaped(self):
        text = subtitle_filter(Path(r"C:\Видео тест\клип 01.srt"), SubtitleStyle())
        self.assertTrue(text.startswith("subtitles='C\\:/Видео тест/клип 01.srt'"))
        self.assertNotIn(r"\Видео", text)              # обратные слэши убраны
        self.assertIn(":force_style='", text)

    def test_style_is_quoted(self):
        text = subtitle_filter(Path("clip.srt"), SubtitleStyle())
        style = text.split(":force_style='", 1)[1]
        self.assertTrue(style.endswith("'"))
        self.assertIn(",", style)                      # запятые внутри кавычек


class TestAvailability(unittest.TestCase):
    def test_reason_matches_availability(self):
        if is_available():
            self.assertEqual(unavailable_reason(), "")
        else:
            self.assertIn("pip install faster-whisper", unavailable_reason())

    def test_estimate(self):
        self.assertEqual(estimate_seconds(60), (60.0, 180.0))
        self.assertEqual(estimate_seconds(0), (0.0, 0.0))

    @unittest.skipIf(is_available(), "faster-whisper установлен — проверяется рабочий путь")
    def test_filter_for_raises_without_module(self):
        folder = make_temp_folder("nocall")
        info = VideoInfo(path=folder / "нет.mp4", duration=10, width=640, height=360,
                         fps=30, has_audio=True)
        task = SubtitleTask(info, folder / "work", SubtitleStyle())
        try:
            with self.assertRaises(SubtitleUnavailable):
                task.filter_for(0, type("Clip", (), {"start": 0.0, "duration": 3.0})())
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_cleanup_removes_temp_files(self):
        folder = make_temp_folder("cleanup")
        work = folder / "work"
        work.mkdir()
        info = VideoInfo(path=folder / "a.mp4", duration=10, width=640, height=360,
                         fps=30, has_audio=True)
        task = SubtitleTask(info, work, SubtitleStyle())
        wav = work / "clip_01.wav"
        srt = work / "clip_01.srt"
        wav.write_bytes(b"x")
        srt.write_text("1\n", encoding="utf-8")
        task._created += [wav, srt]
        try:
            task.cleanup()
            self.assertFalse(wav.exists())
            self.assertFalse(srt.exists())
            self.assertFalse(work.exists())
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_cleanup_removes_readonly_work_dir(self):
        """Пустая рабочая папка убирается и с атрибутом «только чтение».

        Так помечает каталоги OneDrive, и тогда обычное удаление отвечает
        «отказано в доступе» (WinError 5) — из-за этого после нарезки в папке
        вывода оставалась пустая папка _subs.
        """
        import os
        import stat

        folder = make_temp_folder("cleanup_readonly")
        work = folder / "work"
        work.mkdir()
        info = VideoInfo(path=folder / "a.mp4", duration=10, width=640, height=360,
                         fps=30, has_audio=True)
        task = SubtitleTask(info, work, SubtitleStyle())
        wav = work / "clip_01.wav"
        wav.write_bytes(b"x")
        task._created += [wav]
        os.chmod(work, stat.S_IREAD)              # атрибут «только чтение»
        try:
            task.cleanup()
            self.assertFalse(wav.exists())
            self.assertFalse(work.exists(),
                             "пустая рабочая папка должна удаляться и с этим атрибутом")
        finally:
            try:
                os.chmod(work, stat.S_IWRITE)
            except OSError:
                pass
            shutil.rmtree(folder, ignore_errors=True)


class TestLocalModel(unittest.TestCase):
    """Готовая модель ищется рядом с программой (офлайн, без повторной загрузки)."""

    def test_custom_path_passes_through(self):
        self.assertEqual(subtitles.local_model_path("tiny"), "tiny")
        self.assertEqual(subtitles.local_model_path("D:/models/whisper"), "D:/models/whisper")

    @unittest.skipUnless((PROJECT_ROOT / ".hf-cache" / "faster-whisper-small"
                          / "model.bin").exists(), "локальной модели нет")
    def test_finds_local_model(self):
        found = subtitles.local_model_path()
        self.assertTrue(found.endswith("faster-whisper-small"), found)
        self.assertTrue(Path(found, "model.bin").exists())


class TestAudioReading(unittest.TestCase):
    """Звук читается стандартным wave и numpy — без библиотеки av."""

    def _numpy(self):
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy не установлен (появляется вместе с faster-whisper)")
            raise
        return numpy

    def _write_wav(self, path: Path, samples: list[int], channels: int = 1) -> None:
        import struct
        import wave

        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    def test_mono_values(self):
        numpy = self._numpy()
        folder = make_temp_folder("wav")
        path = folder / "тест.wav"
        try:
            self._write_wav(path, [0, 16384, -16384, 32767, -32768])
            data = read_wav_mono(path)
            self.assertEqual(data.shape[0], 5)
            self.assertAlmostEqual(float(data[1]), 0.5, places=4)
            self.assertAlmostEqual(float(data[3]), 32767 / 32768, places=5)
            self.assertEqual(str(data.dtype), "float32")
            self.assertLessEqual(float(numpy.max(numpy.abs(data))), 1.0)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_stereo_is_mixed_to_mono(self):
        self._numpy()
        folder = make_temp_folder("wav_stereo")
        path = folder / "стерео.wav"
        try:
            self._write_wav(path, [1000, 3000, 1000, 3000], channels=2)
            data = read_wav_mono(path)
            self.assertEqual(data.shape[0], 2)          # каналы сложились
            self.assertAlmostEqual(float(data[0]), 2000 / 32768, places=5)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class TestRecognitionUsesArray(unittest.TestCase):
    """Регрессия: модель должна получать массив, а не путь к файлу.

    Если передать faster-whisper путь, он декодирует звук через PyAV, а в
    av 19 (PyAV 19) параметр metadata_errors убран — распознавание падает с
    TypeError: open() got an unexpected keyword argument 'metadata_errors'.
    Поэтому приложение читает WAV само (read_wav_mono) и отдаёт массив.
    """

    class _FakeModel:
        """Заглушка модели: запоминает, что именно ей передали."""

        def __init__(self):
            self.audio = None
            self.kwargs: dict = {}

        def transcribe(self, audio, **kwargs):
            self.audio = audio
            self.kwargs = kwargs
            return [], None            # ни одного сегмента — субтитров не будет

    def test_model_gets_array_with_vad(self):
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy не установлен (появляется вместе с faster-whisper)")
        import struct
        import wave
        from unittest import mock

        folder = make_temp_folder("recognition")
        model = self._FakeModel()
        clip = type("Clip", (), {"start": 0.0, "duration": 0.1})()
        info = VideoInfo(path=folder / "a.mp4", duration=10, width=640, height=360,
                         fps=30, has_audio=True)
        task = SubtitleTask(info, folder / "work", SubtitleStyle())

        def fake_extract(_info, _start, _duration, target):
            """Вместо ffmpeg пишем готовый WAV туда, куда его ждёт приложение."""
            target.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(target), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(struct.pack("<1600h", *([1000] * 1600)))
            return target

        try:
            with mock.patch.object(subtitles, "is_available", return_value=True), \
                    mock.patch.object(subtitles, "_load_model", return_value=model), \
                    mock.patch.object(subtitles, "extract_audio", side_effect=fake_extract):
                result = task.filter_for(0, clip)
            self.assertIsNone(result)
            self.assertIsInstance(model.audio, numpy.ndarray)      # массив, а не путь
            self.assertTrue(model.kwargs.get("vad_filter"))
            self.assertEqual(model.kwargs.get("beam_size"), 5)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
