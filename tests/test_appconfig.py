# -*- coding: utf-8 -*-
"""Тесты сохранения настроек между запусками.

Файл настроек по умолчанию лежит в %APPDATA%, но в тестах APPDATA подменяется
на папку внутри проекта — так проверка не зависит от системного профиля.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import appconfig  # noqa: E402
from appconfig import (SETTING_DEFAULTS, config_path, load_settings,  # noqa: E402
                       reset_settings, save_settings)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_temp_appdata(name: str) -> Path:
    folder = PROJECT_ROOT / f"_tmp_cfg_{name}"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class TestSettings(unittest.TestCase):
    def setUp(self) -> None:
        self.appdata = make_temp_appdata(self._testMethodName)
        self.patch = mock.patch.dict(os.environ, {"APPDATA": str(self.appdata)})
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        shutil.rmtree(self.appdata, ignore_errors=True)

    def test_path_inside_appdata(self):
        path = config_path()
        self.assertEqual(path.parent.parent, self.appdata)
        self.assertEqual(path.name, "settings.json")
        self.assertIn("ShortsCutter", str(path))

    def test_defaults_when_no_file(self):
        values = load_settings()
        self.assertEqual(values["crf"], SETTING_DEFAULTS["crf"])
        self.assertEqual(values["format"], SETTING_DEFAULTS["format"])
        self.assertEqual(values["subtitles"], 0)

    def test_save_and_load_roundtrip(self):
        values = dict(SETTING_DEFAULTS)
        values.update({"crf": "24", "format": "Оставить как есть", "subtitles": 1,
                       "chunk_len": "45", "output_dir": r"C:\Видео тест\Shorts",
                       "output_dir_custom": True, "last_dir": r"C:\Видео тест"})
        self.assertTrue(save_settings(values))
        self.assertTrue(config_path().exists())

        loaded = load_settings()
        self.assertEqual(loaded["crf"], "24")
        self.assertEqual(loaded["format"], "Оставить как есть")
        self.assertEqual(loaded["subtitles"], 1)
        self.assertEqual(loaded["chunk_len"], "45")
        self.assertEqual(loaded["output_dir"], r"C:\Видео тест\Shorts")
        self.assertIs(loaded["output_dir_custom"], True)
        self.assertEqual(loaded["last_dir"], r"C:\Видео тест")

    def test_file_is_readable_utf8_without_escapes(self):
        values = dict(SETTING_DEFAULTS)
        values["output_dir"] = r"C:\Видео тест\Shorts"
        save_settings(values)
        text = config_path().read_text(encoding="utf-8")
        self.assertIn("Видео тест", text)          # кириллица не в \\u-escape
        self.assertEqual(json.loads(text)["output_dir"], values["output_dir"])

    def test_corrupted_file_falls_back(self):
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{это не json", encoding="utf-8")
        self.assertEqual(load_settings()["crf"], SETTING_DEFAULTS["crf"])

    def test_wrong_types_fall_back(self):
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"crf": ["20"], "subtitles": "да", "unknown": 5}),
                        encoding="utf-8")
        loaded = load_settings()
        self.assertEqual(loaded["crf"], SETTING_DEFAULTS["crf"])
        self.assertEqual(loaded["subtitles"], 0)
        self.assertNotIn("unknown", loaded)        # лишние ключи не сохраняем

    def test_extra_keys_are_dropped_on_save(self):
        values = dict(SETTING_DEFAULTS)
        values["что_то_лишнее"] = 42
        save_settings(values)
        saved = json.loads(config_path().read_text(encoding="utf-8"))
        self.assertNotIn("что_то_лишнее", saved)

    def test_reset(self):
        values = dict(SETTING_DEFAULTS)
        values["crf"] = "28"
        save_settings(values)
        self.assertTrue(reset_settings())
        self.assertEqual(load_settings()["crf"], SETTING_DEFAULTS["crf"])

    def test_no_temp_files_left(self):
        save_settings(dict(SETTING_DEFAULTS))
        leftovers = list(config_path().parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_save_is_quiet_on_failure(self):
        with mock.patch.object(appconfig.Path, "mkdir",
                               side_effect=PermissionError("нет доступа")):
            self.assertFalse(save_settings(dict(SETTING_DEFAULTS)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
