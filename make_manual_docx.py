# -*- coding: utf-8 -*-
"""Сборка руководства пользователя в Word из КАК-ПОЛЬЗОВАТЬСЯ.md.

Запуск:  py -3 make_manual_docx.py
Нужен пакет python-docx:  pip install python-docx

Зачем: руководство ведётся текстом (markdown — его удобно править и смотреть
разницу), а рядом лежит та же инструкция в Word — её удобно распечатать или
переслать человеку, который не работает с гитхабом. Этот скрипт собирает одно
из другого, чтобы версии не разъезжались.

Что понимает из markdown: заголовки (#, ##, ###), списки (- и «1. »), таблицы,
цитаты (>), блоки кода (```), **жирный** и `моноширинный` внутри строки.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = Path(__file__).resolve().parent
SRC = HERE / "КАК-ПОЛЬЗОВАТЬСЯ.md"
OUT = HERE / "КАК-ПОЛЬЗОВАТЬСЯ.docx"

BODY_FONT = "Segoe UI"
CODE_FONT = "Consolas"
MUTED = RGBColor(0x55, 0x55, 0x55)


def set_base_style(document: Document) -> None:
    """Базовый шрифт и отступы документа."""
    style = document.styles["Normal"]
    style.font.name = BODY_FONT
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.08


def add_runs(paragraph, text: str) -> None:
    """Разложить строку на обычный текст, **жирный** и `код`."""
    for chunk in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**") and len(chunk) > 4:
            run = paragraph.add_run(chunk[2:-2])
            run.bold = True
        elif chunk.startswith("`") and chunk.endswith("`") and len(chunk) > 2:
            run = paragraph.add_run(chunk[1:-1])
            run.font.name = CODE_FONT
            run.font.size = Pt(9.5)
        else:
            paragraph.add_run(chunk)


def strip_anchor(text: str) -> str:
    """Ссылку вида [текст](#якорь) превратить просто в текст."""
    return re.sub(r"\[([^\]]+)\]\(#[^)]*\)", r"\1", text)


def add_table(document: Document, rows: list[list[str]]) -> None:
    """Таблица markdown → таблица Word с рамкой."""
    if not rows:
        return
    width = max(len(row) for row in rows)
    table = document.add_table(rows=0, cols=width)
    table.style = "Table Grid"
    table.autofit = True
    for index, row in enumerate(rows):
        cells = table.add_row().cells
        for column in range(width):
            text = row[column] if column < len(row) else ""
            paragraph = cells[column].paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(2)
            add_runs(paragraph, text)
            for run in paragraph.runs:
                run.font.size = Pt(9.5)
                if index == 0:
                    run.bold = True
    document.add_paragraph()


def build() -> int:
    if not SRC.exists():
        print(f"нет файла {SRC}")
        return 1

    lines = SRC.read_text(encoding="utf-8").splitlines()
    document = Document()
    set_base_style(document)
    for section in document.sections:
        section.left_margin = section.right_margin = Cm(2)
        section.top_margin = section.bottom_margin = Cm(1.8)

    in_code = False
    code: list[str] = []
    table_rows: list[list[str]] = []

    def flush_table() -> None:
        nonlocal table_rows
        if table_rows:
            add_table(document, table_rows)
            table_rows = []

    def flush_code() -> None:
        nonlocal code
        if not code:
            return
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.left_indent = Cm(0.5)
        paragraph.paragraph_format.space_after = Pt(8)
        for number, line in enumerate(code):
            run = paragraph.add_run(("\n" if number else "") + line)
            run.font.name = CODE_FONT
            run.font.size = Pt(9.5)
            run.font.color.rgb = RGBColor(0x22, 0x22, 0x22)
        code = []

    for raw in lines:
        line = raw.rstrip()

        if line.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_table()
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue

        # Таблицы могут быть с отступом (внутри списка) — это тоже таблица.
        if line.lstrip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", cell or "-") for cell in cells):
                continue                     # строка-разделитель
            table_rows.append(cells)
            continue
        flush_table()

        if not line.strip():
            continue

        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            text = strip_anchor(line.lstrip("#").strip())
            if level == 1:
                document.add_heading(text, level=0).alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                document.add_heading(text, level=min(level - 1, 3))
            continue

        if line.startswith("> "):
            add_runs(document.add_paragraph(style="Intense Quote"), line[2:])
            continue

        if line.strip() == "---":
            continue

        if re.match(r"^[-*] ", line.strip()):
            add_runs(document.add_paragraph(style="List Bullet"), line.strip()[2:])
            continue
        if re.match(r"^\d+\. ", line.strip()):
            add_runs(document.add_paragraph(style="List Number"),
                     re.sub(r"^\d+\. ", "", line.strip()))
            continue

        add_runs(document.add_paragraph(), strip_anchor(line.strip()))

    flush_table()
    flush_code()

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Нарезка Shorts 1.0.3 — руководство пользователя")
    run.font.size = Pt(9)
    run.font.color.rgb = MUTED

    document.save(OUT)
    print(f"готово: {OUT.name} ({OUT.stat().st_size / 1024:.0f} КБ, "
          f"заголовков и таблиц — см. проверку)")
    return 0


if __name__ == "__main__":
    sys.exit(build())
