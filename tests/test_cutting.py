# -*- coding: utf-8 -*-
"""Тесты нарезки: фильтр кропа, команда ffmpeg, имена файлов.

ffmpeg для этих тестов не нужен — проверяются только расчёты и сборка команды.
Запускается вместе с остальными: python main.py --selftest
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cutting import (CutSettings, VideoInfo, build_cut_command,  # noqa: E402
                     clip_output_name, crop_scale_filter, escape_filter_path,
                     unique_output_path)

# Временные папки создаём внутри проекта обычным mkdir: папки модуля tempfile
# в этом окружении недоступны для записи (ограничение песочницы).
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_temp_folder(name: str) -> Path:
    """Пустая временная папка внутри проекта."""
    folder = PROJECT_ROOT / f"_tmp_{name}"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def make_info(width: int, height: int, has_audio: bool = True,
              name: str = "видео.mp4") -> VideoInfo:
    return VideoInfo(path=Path(name), duration=600.0, width=width, height=height,
                     fps=30.0, has_audio=has_audio)


class TestCropScaleFilter(unittest.TestCase):
    def test_4k_crop_before_scale(self):
        text = crop_scale_filter(make_info(3840, 2160), CutSettings())
        self.assertIsNotNone(text)
        self.assertTrue(text.startswith("crop="))
        crop, scale = text.split(",", 1)
        self.assertIn("scale=1080:1920", scale)
        # Кроп должен идти первым — иначе 4K тормозит (п. 9 ТЗ).
        self.assertLess(text.index("crop="), text.index("scale="))
        width, height, x, y = (int(value) for value in crop[5:].split(":"))
        self.assertEqual(height, 2160)          # режем по бокам
        self.assertEqual(width % 2, 0)          # чётные стороны для H.264
        self.assertEqual(x, (3840 - width) // 2)  # по центру
        self.assertEqual(y, 0)
        self.assertAlmostEqual(width / height, 9 / 16, places=2)

    def test_720p(self):
        text = crop_scale_filter(make_info(1280, 720), CutSettings())
        width, height, _x, _y = (int(v) for v in text[5:].split(",")[0].split(":"))
        self.assertEqual((width % 2, height % 2), (0, 0))
        self.assertAlmostEqual(width / height, 9 / 16, places=2)

    def test_tall_source_is_cropped_vertically(self):
        text = crop_scale_filter(make_info(1080, 1920), CutSettings())
        self.assertEqual(text.split(",")[0], "crop=1080:1920:0:0")

    def test_keep_as_is(self):
        settings = CutSettings(vertical=False)
        self.assertIsNone(crop_scale_filter(make_info(1920, 1080), settings))
        # Нечётные стороны всё равно приводим к чётным.
        self.assertEqual(crop_scale_filter(make_info(1921, 1081), settings),
                         "scale=trunc(iw/2)*2:trunc(ih/2)*2")

    def test_unknown_size(self):
        self.assertIsNone(crop_scale_filter(make_info(0, 0), CutSettings()))


class TestCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.info = make_info(1920, 1080)
        self.command = build_cut_command(self.info, 12.5, 3.0,
                                         Path("C:/Видео тест/подкаст_клип01.mp4"),
                                         CutSettings(crf=22))

    def test_seek_before_input(self):
        # Пункт 3.4 ТЗ: -ss и -t перед -i.
        self.assertLess(self.command.index("-ss"), self.command.index("-i"))
        self.assertLess(self.command.index("-t"), self.command.index("-i"))
        self.assertEqual(self.command[self.command.index("-ss") + 1], "12.500")
        self.assertEqual(self.command[self.command.index("-t") + 1], "3.000")

    def test_no_stream_copy(self):
        # Копирование потока сдвинуло бы границы по ключевым кадрам.
        for index, item in enumerate(self.command[:-1]):
            if item == "-c":
                self.assertNotEqual(self.command[index + 1], "copy")

    def test_encoding_parameters(self):
        self.assertIn("libx264", self.command)
        self.assertIn("veryfast", self.command)
        self.assertIn("yuv420p", self.command)
        self.assertEqual(self.command[self.command.index("-crf") + 1], "22")
        self.assertIn("aac", self.command)
        self.assertIn("128k", self.command)

    def test_output_is_last(self):
        self.assertEqual(self.command[-1], "C:\\Видео тест\\подкаст_клип01.mp4")
        self.assertIn("-y", self.command)

    def test_without_audio(self):
        command = build_cut_command(make_info(1920, 1080, has_audio=False), 0, 2,
                                    Path("out.mp4"), CutSettings())
        self.assertNotIn("aac", command)

    def test_crf_is_clamped(self):
        self.assertEqual(CutSettings(crf=40).clamped_crf(), 28)
        self.assertEqual(CutSettings(crf=5).clamped_crf(), 16)
        command = build_cut_command(self.info, 0, 1, Path("out.mp4"), CutSettings(crf=99))
        self.assertEqual(command[command.index("-crf") + 1], "28")

    def test_subtitles_filter_added(self):
        """Фильтр субтитров идёт последним в -vf (этап 3)."""
        from subtitles import SubtitleStyle, subtitle_filter

        fragment = subtitle_filter(Path("C:/Видео тест/суб титры.srt"), SubtitleStyle())
        command = build_cut_command(self.info, 0, 1, Path("out.mp4"), CutSettings(),
                                    subtitles_filter=fragment)
        filters = command[command.index("-vf") + 1]
        self.assertTrue(filters.endswith(fragment))
        self.assertLess(filters.index("crop="), filters.index("subtitles="))
        self.assertIn("Видео тест", filters)
        self.assertIn("суб титры.srt", filters)   # пробелы в пути не ломают фильтр

    def test_escape_helper(self):
        text = escape_filter_path(Path(r"C:\Users\nadop\Видео тест\a'b.srt"))
        self.assertTrue(text.startswith("'") and text.endswith("'"))
        self.assertIn("C\\:/Users", text)
        self.assertIn(r"\'", text)


class TestOutputNames(unittest.TestCase):
    def test_names(self):
        self.assertEqual(clip_output_name("подкаст", 0), "подкаст_клип01.mp4")
        self.assertEqual(clip_output_name("подкаст", 9), "подкаст_клип10.mp4")

    def test_no_overwrite(self):
        path = make_temp_folder("no_overwrite")
        try:
            first = unique_output_path(path, "ролик", 0)
            self.assertEqual(first.name, "ролик_клип01.mp4")
            first.write_bytes(b"x")
            second = unique_output_path(path, "ролик", 0)
            self.assertEqual(second.name, "ролик_клип01_2.mp4")
            second.write_bytes(b"x")
            third = unique_output_path(path, "ролик", 0)
            self.assertEqual(third.name, "ролик_клип01_3.mp4")
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_cyrillic_and_spaces(self):
        folder = make_temp_folder("cyrillic")
        path = folder / "Видео тест"
        path.mkdir()
        try:
            result = unique_output_path(path, "длинный ролик 5 минут", 1)
            self.assertEqual(result.name, "длинный ролик 5 минут_клип02.mp4")
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
