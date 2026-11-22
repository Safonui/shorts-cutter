# -*- coding: utf-8 -*-
"""Тесты парсера таймкодов и проверки интервалов.

Запуск:  python main.py --selftest
     или python -m unittest discover -s tests -t .
Интерфейс и ffmpeg для этих тестов не нужны.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intervals import (Clip, ParseError, format_duration, format_timecode,  # noqa: E402
                       issues_summary, parse_interval, parse_timecode, split_into_chunks,
                       validate_clips)


class TestParseTimecode(unittest.TestCase):
    def test_minutes_seconds(self):
        self.assertAlmostEqual(parse_timecode("1:23"), 83.0)
        self.assertAlmostEqual(parse_timecode("00:10"), 10.0)

    def test_hours_minutes_seconds(self):
        self.assertAlmostEqual(parse_timecode("01:23:45"), 5025.0)
        self.assertAlmostEqual(parse_timecode("1:05:00"), 3900.0)

    def test_plain_seconds(self):
        self.assertAlmostEqual(parse_timecode("95"), 95.0)

    def test_minutes_over_59(self):
        # Видео длиннее часа: в форме ММ:СС минуты могут быть больше 59.
        self.assertAlmostEqual(parse_timecode("125:30"), 7530.0)

    def test_seconds_limit(self):
        with self.assertRaises(ParseError):
            parse_timecode("1:75")

    def test_empty_and_garbage(self):
        for bad in ("", "   ", "абв", "1:2:3:4", "-5"):
            with self.assertRaises(ParseError):
                parse_timecode(bad)


class TestParseInterval(unittest.TestCase):
    """Критерий приёмки № 2 из ТЗ."""

    def test_acceptance_cases(self):
        self.assertEqual(parse_interval("12:30-13:20"), (750.0, 800.0))
        self.assertEqual(parse_interval("1:05:00-1:06:00"), (3900.0, 3960.0))
        self.assertEqual(parse_interval("0:30-0:45"), (30.0, 45.0))

    def test_spaces_and_mixed_formats(self):
        self.assertEqual(parse_interval("00:10 - 00:30"), (10.0, 30.0))
        self.assertEqual(parse_interval("  1:23-2:45  "), (83.0, 165.0))
        self.assertEqual(parse_interval("01:23:45-01:25:00"), (5025.0, 5100.0))
        self.assertEqual(parse_interval("1:23:45-1:25:00"), (5025.0, 5100.0))

    def test_reversed_interval_parses_but_is_invalid(self):
        # «13:20-12:30» разбирается, но проверка обязана пометить его ошибкой.
        start, end = parse_interval("13:20-12:30")
        self.assertGreater(start, end)
        issues = validate_clips([Clip(start, end)], duration=3600.0)
        self.assertTrue(any("не позже начала" in issue.message for issue in issues))

    def test_bad_intervals(self):
        for bad in ("13:20", "13:20-", "-13:20", "1:2:3-4:5:6:7", "13:20-12:30-11:00"):
            with self.assertRaises(ParseError):
                parse_interval(bad)


class TestValidate(unittest.TestCase):
    def test_out_of_bounds(self):
        issues = validate_clips([Clip(10.0, 20.0), Clip(100.0, 200.0)], duration=120.0)
        messages = [issue.message for issue in issues if issue.severity == "error"]
        self.assertTrue(any("выходит за пределы" in text for text in messages))

    def test_overlap_both_sides(self):
        clips = [Clip(0.0, 60.0), Clip(30.0, 90.0)]
        issues = validate_clips(clips, duration=600.0)
        overlap = [issue for issue in issues if "пересекается" in issue.message]
        self.assertEqual({issue.index for issue in overlap}, {0, 1})
        self.assertEqual(len(overlap), 2)

    def test_overlap_containing_clip(self):
        clips = [Clip(0.0, 100.0), Clip(10.0, 20.0), Clip(30.0, 40.0)]
        issues = validate_clips(clips, duration=600.0)
        flagged = {issue.index for issue in issues if "пересекается" in issue.message}
        self.assertEqual(flagged, {0, 1, 2})

    def test_touching_intervals_are_ok(self):
        issues = validate_clips([Clip(0.0, 30.0), Clip(30.0, 60.0)], duration=600.0)
        self.assertEqual([i for i in issues if i.severity == "error"], [])

    def test_unknown_duration_gives_warning(self):
        issues = validate_clips([Clip(0.0, 30.0)])
        self.assertTrue(any(issue.severity == "warning" for issue in issues))

    def test_bounds_tolerance(self):
        issues = validate_clips([Clip(0.0, 120.2)], duration=120.0)
        self.assertEqual([i for i in issues if i.severity == "error"], [])

    def test_summary_text(self):
        self.assertIn("корректны", issues_summary([]))
        self.assertIn("пересекается", issues_summary(validate_clips(
            [Clip(0.0, 60.0), Clip(30.0, 90.0)], duration=600.0)))


class TestFormatting(unittest.TestCase):
    def test_timecode(self):
        self.assertEqual(format_timecode(83), "01:23")
        self.assertEqual(format_timecode(3725), "01:02:05")
        self.assertEqual(format_timecode(0), "00:00")

    def test_duration(self):
        self.assertEqual(format_duration(12.0), "12 с")
        self.assertEqual(format_duration(12.5), "12.5 с")
        self.assertEqual(format_duration(0.0), "0 с")


class TestSplitIntoChunks(unittest.TestCase):
    """«Нарезать подряд»: равные клипы по всему видео (этап 4)."""

    def test_even_division(self):
        clips = split_into_chunks(120.0, 30.0)
        self.assertEqual(len(clips), 4)
        self.assertEqual([c.duration for c in clips], [30.0] * 4)
        self.assertEqual((clips[0].start, clips[-1].end), (0.0, 120.0))

    def test_tail_is_kept_when_long_enough(self):
        clips = split_into_chunks(125.0, 60.0)
        self.assertEqual(len(clips), 3)
        self.assertAlmostEqual(clips[-1].duration, 5.0, places=3)
        self.assertAlmostEqual(clips[-1].end, 125.0, places=3)

    def test_short_tail_is_dropped(self):
        clips = split_into_chunks(123.0, 60.0)
        self.assertEqual(len(clips), 2)
        self.assertAlmostEqual(clips[-1].end, 120.0, places=3)

    def test_video_shorter_than_chunk(self):
        clips = split_into_chunks(3.0, 60.0)
        self.assertEqual(len(clips), 1)
        self.assertAlmostEqual(clips[0].duration, 3.0, places=3)

    def test_chunks_do_not_overlap(self):
        clips = split_into_chunks(600.0, 45.0)
        self.assertTrue(all(clips[i].end <= clips[i + 1].start + 0.001
                            for i in range(len(clips) - 1)))
        self.assertEqual(validate_clips(clips, duration=600.0), [])

    def test_long_video(self):
        # 4 часа по 30 секунд — 480 клипов, все внутри видео.
        clips = split_into_chunks(4 * 3600.0, 30.0)
        self.assertEqual(len(clips), 480)
        self.assertAlmostEqual(clips[-1].end, 4 * 3600.0, places=3)

    def test_bad_arguments(self):
        self.assertEqual(split_into_chunks(0, 30), [])
        self.assertEqual(split_into_chunks(100, 0), [])
        self.assertEqual(split_into_chunks(-10, 30), [])
        self.assertEqual(split_into_chunks(100, -5), [])

    def test_short_video_with_several_chunks(self):
        """Регрессия: 6 с по 2 с — это три клипа, а не один."""
        clips = split_into_chunks(6.0, 2.0)
        self.assertEqual([(c.start, c.end) for c in clips],
                         [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)])

    def test_many_chunks_then_short_tail(self):
        self.assertEqual(len(split_into_chunks(7.0, 2.0)), 3)     # хвост 1 с отброшен
        self.assertEqual(len(split_into_chunks(10.0, 2.0)), 5)
        self.assertEqual(len(split_into_chunks(12.5, 2.0)), 6)

    def test_tail_exactly_min_tail(self):
        self.assertEqual(len(split_into_chunks(16.0, 6.0)), 2)    # хвост 4 с отброшен
        self.assertEqual(len(split_into_chunks(17.0, 6.0)), 3)    # хвост 5 с оставлен

    def test_custom_min_tail(self):
        self.assertEqual(len(split_into_chunks(121.0, 60.0, min_tail=1.0)), 3)
        self.assertEqual(len(split_into_chunks(121.0, 60.0, min_tail=10.0)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
