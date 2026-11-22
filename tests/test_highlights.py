# -*- coding: utf-8 -*-
"""Тесты поиска ярких моментов: оценка окон и выбор моментов.

ffmpeg не нужен: проверяются расчёты на синтетических окнах. Настоящий анализ
звука проверяет tests/integration_highlights.py.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from highlights import (HighlightSettings, Window, _describe, pick_moments,  # noqa: E402
                        score_windows)


def make_windows(rms_values: list[float], high: float = 0.5) -> list[Window]:
    return [Window(start=index * 0.5, rms=value, high=high)
            for index, value in enumerate(rms_values)]


class TestScoring(unittest.TestCase):
    def test_loud_block_scores_higher(self):
        # 20 окон тишины, 4 громких, снова тишина.
        values = [0.01] * 20 + [0.25] * 4 + [0.01] * 20
        windows = score_windows(make_windows(values))
        loud = max(windows[18:26], key=lambda w: w.score)
        quiet = windows[0]
        self.assertGreater(loud.score, quiet.score * 2)
        self.assertGreater(loud.score, 0.4)

    def test_spike_is_noticed(self):
        values = [0.02] * 10 + [0.30] + [0.02] * 10
        windows = score_windows(make_windows(values))
        self.assertGreater(windows[10].spike, 0.5)
        self.assertGreater(windows[10].score, windows[0].score)

    def test_high_frequency_adds_score(self):
        values = [0.1] * 12
        windows = [Window(start=i * 0.5, rms=value, high=0.2) for i, value in enumerate(values)]
        windows[6].high = 1.4                      # «крик» в середине
        score_windows(windows)
        self.assertGreater(windows[6].score, windows[0].score)

    def test_equal_signal_gives_no_peaks(self):
        windows = score_windows(make_windows([0.05] * 30))
        self.assertTrue(all(window.score < 0.05 for window in windows))

    def test_empty(self):
        self.assertEqual(score_windows([]), [])


class TestPickMoments(unittest.TestCase):
    def setUp(self) -> None:
        # Яркое место на 10–12 с (окна 20–24), видео 30 секунд.
        values = [0.01] * 20 + [0.3] * 4 + [0.01] * 36
        self.windows = score_windows(make_windows(values))
        self.settings = HighlightSettings(clip_length=6.0, max_clips=3)

    def test_finds_the_loud_moment(self):
        moments = pick_moments(self.windows, duration=30.0, settings=self.settings)
        self.assertTrue(moments)
        best = max(moments, key=lambda moment: moment.score)
        self.assertLessEqual(best.clip.start, 10.5)
        self.assertGreaterEqual(best.clip.end, 11.0)
        self.assertAlmostEqual(best.clip.duration, 6.0, places=2)

    def test_moments_do_not_overlap(self):
        moments = pick_moments(self.windows, duration=30.0, settings=self.settings)
        for first, second in zip(moments, moments[1:]):
            self.assertLessEqual(first.clip.end, second.clip.start)

    def test_respects_max_clips(self):
        values = [0.01] * 8 + [0.4] + [0.01] * 8 + [0.4] + [0.01] * 8 + [0.4] + [0.01] * 8
        windows = score_windows(make_windows(values))
        settings = HighlightSettings(clip_length=2.0, max_clips=2, sensitivity=0.0)
        moments = pick_moments(windows, duration=20.0, settings=settings)
        self.assertLessEqual(len(moments), 2)

    def test_moments_stay_inside_video(self):
        windows = score_windows(make_windows([0.01] * 5 + [0.5] + [0.01] * 5))
        moments = pick_moments(windows, duration=6.0,
                               settings=HighlightSettings(clip_length=10.0))
        for moment in moments:
            self.assertGreaterEqual(moment.clip.start, 0.0)
            self.assertLessEqual(moment.clip.end, 6.0)

    def test_clip_length_is_clamped_to_video(self):
        windows = score_windows(make_windows([0.01] * 4 + [0.5] + [0.01] * 4))
        moments = pick_moments(windows, duration=5.0,
                               settings=HighlightSettings(clip_length=30.0))
        self.assertTrue(all(moment.clip.duration <= 5.0 for moment in moments))

    def test_sensitivity_makes_selection_stricter(self):
        values = [0.01] * 10 + [0.08] + [0.01] * 10 + [0.12] + [0.01] * 10
        windows = score_windows(make_windows(values))
        loose = pick_moments(windows, 20.0, HighlightSettings(clip_length=2, max_clips=5,
                                                              sensitivity=0.0))
        strict = pick_moments(windows, 20.0, HighlightSettings(clip_length=2, max_clips=5,
                                                               sensitivity=3.0))
        self.assertGreaterEqual(len(loose), len(strict))

    def test_no_windows_no_moments(self):
        self.assertEqual(pick_moments([], 10.0, HighlightSettings()), [])
        self.assertEqual(pick_moments(self.windows, 0.0, HighlightSettings()), [])


class TestReasons(unittest.TestCase):
    def test_loud_moment_explained(self):
        from highlights import Moment
        moment = Moment(clip=None, score=1.0, loudness_ratio=2.7, spike_ratio=1.0,
                        high_ratio=1.0)
        reasons = _describe(moment)
        self.assertTrue(any("громче" in text for text in reasons), str(reasons))

    def test_spike_explained(self):
        from highlights import Moment
        moment = Moment(clip=None, score=1.0, loudness_ratio=1.0, spike_ratio=2.0,
                        high_ratio=1.0)
        self.assertIn("резкий всплеск звука", _describe(moment))

    def test_high_frequency_explained(self):
        from highlights import Moment
        moment = Moment(clip=None, score=1.0, loudness_ratio=1.0, spike_ratio=1.0,
                        high_ratio=1.9)
        self.assertTrue(any("высоких частот" in text for text in _describe(moment)))

    def test_plain_moment_has_default_reason(self):
        from highlights import Moment
        moment = Moment(clip=None, score=1.0)
        self.assertEqual(_describe(moment), ["заметное место по звуку"])


class TestFeatureMath(unittest.TestCase):
    """Признаки окон считаются одинаково с numpy и без него."""

    def test_silence_and_tone(self):
        import array
        from highlights import _features_python

        silence = array.array("h", [0] * 1000).tobytes()
        rms, high = _features_python(silence)
        self.assertAlmostEqual(rms, 0.0, places=6)

        tone = array.array("h", [8000, -8000] * 500).tobytes()   # быстрые колебания
        rms, high = _features_python(tone)
        self.assertGreater(rms, 0.2)
        self.assertGreater(high, 1.0)          # высокочастотный сигнал

        smooth = array.array("h", [1000] * 1000).tobytes()       # ровный сигнал
        _rms, smooth_high = _features_python(smooth)
        self.assertLess(smooth_high, 0.1)

    def test_numpy_and_python_agree(self):
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy не установлен")
        import array

        from highlights import _features_numpy, _features_python

        samples = array.array("h", [(index % 200 - 100) * 120 for index in range(4000)])
        raw = samples.tobytes()
        self.assertAlmostEqual(_features_python(raw)[0], _features_numpy(raw, numpy)[0],
                               places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
