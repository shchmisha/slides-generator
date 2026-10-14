"""Instructions hidden in materials: text addressed to the model rather than to the reader.

A document is data. A paragraph that tries to steer the model — "игнорируй правила",
"примечание редактора для сценариста: скажи …", "в derived добавь scale …" — is removed before
the model sees the materials and reported to the user as a data note. The system prompt says
the same thing; this is the second, deterministic line of defence, and the number rules
(placeholders, no free constants) are the third.

Detection is conservative: ordinary business text ("Прошу подготовить презентацию", "верьте
таблице", "Подпишите доверенность") must pass. It needs an explicit override phrase, a direct
address to an AI/scriptwriter/editor together with a command, or several words of our own
scenario schema, which no business document uses.
"""

from __future__ import annotations

import re
import unicodedata

from app.facts.models import Cell, Column, Row, Table
from app.ingest.models import Block, Document, Note

_OVERRIDE = re.compile(
    r"игнорир\w*|не учитыва\w* (?:правил|инструкц|предыдущ|указани)"
    r"|забудь\w* (?:вс|правил|инструкц)"
    r"|таблиц\w* не вер\w*|не вер\w* таблиц\w*|вместо (?:данных|цифр) из таблиц"
    r"|важнее (?:тво\w+|любых|всех) правил|с этого момента ты|нов\w+ инструкци\w*"
    r"|системн\w+ (?:указани|инструкци|сообщени|промпт)|не упоминай|никому не сообщай"
    r"|ignore (?:all|any|previous|the above)|disregard (?:all|previous)|system prompt",
    re.IGNORECASE,
)
_ADDRESSEE = re.compile(
    r"\bдля (?:ии|модели|нейросети|ассистента|сценариста|редактора"
    r"|автора (?:сценари\w*|ролика|слайдов))\b"
    r"|\bии-(?:помощник|ассистент|модел)\w*|\bассистент\w*|\bсценарист\w*|\bавтор\w* сценари\w*"
    r"|примечани\w* редактор\w*|\bты (?:—|-|теперь|должен|должна|будешь)"
    r"|\b(?:chatgpt|gpt|qwen|claude|llm)\b",
    re.IGNORECASE,
)
_COMMAND = re.compile(
    r"\b(?:добавь|напиши|выведи|покажи|укажи|поставь|замени|скажи|сообщи|оформи|сделай"
    r"|используй|пиши|не показывай|должен|должна|должны|должно|обязательно|нужно)\b",
    re.IGNORECASE,
)
_SCHEMA_WORD = re.compile(
    r"\b(?:derived|values|claims|scale|factor|percent_of|percent_change|key_takeaways"
    r"|chart_table|cost_structure)\b|\{[a-z_]+(?:\[[^\]]*\])?\}",
    re.IGNORECASE,
)
# Latin letters that look Cyrillic, as used to disguise trigger words ("игнoрируй").
_LOOKALIKE = str.maketrans("aceopxyktmhb", "асеорхуктмнв")
MAX_SHOWN = 160


def _forms(text: str) -> tuple[str, str]:
    normal = unicodedata.normalize("NFKC", text)
    normal = re.sub("[​-‏⁠-⁤﻿]", "", normal).casefold()
    return normal, normal.translate(_LOOKALIKE)


def instruction_reason(text: str) -> str | None:
    """Why the text looks like an instruction to the model, or None for ordinary text."""
    for form in _forms(text):
        if _OVERRIDE.search(form):
            return "команда модели"
        if _ADDRESSEE.search(form) and _COMMAND.search(form):
            return "обращение к модели с указанием"
        if len(_SCHEMA_WORD.findall(form)) >= 2:
            return "служебные слова сценария"
    return None


def _shorten(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_SHOWN else text[: MAX_SHOWN - 1] + "…"


def _note(source: str, location: str | None, reason: str, text: str) -> Note:
    return Note(
        severity="warning",
        source=source,
        location=location,
        message=f"Похоже на указание для модели ({reason}) — не использовано: «{_shorten(text)}»",
        kind="quarantine",
    )


def quarantine_documents(documents: list[Document]) -> tuple[list[Document], list[Note]]:
    notes: list[Note] = []
    cleaned = []
    for document in documents:
        kept: list[Block] = []
        for block in document.blocks:
            reason = instruction_reason(block.text)
            if reason is None:
                kept.append(block)
            else:
                notes.append(_note(document.filename, None, reason, block.text))
        cleaned.append(document.model_copy(update={"blocks": kept}))
    return cleaned, notes


def quarantine_tables(tables: list[Table]) -> tuple[list[Table], list[Note]]:
    """Text inside tables is checked too: the title, the header of the label column, column
    and row labels, text cells. A match is masked. Masked labels are numbered, so they stay
    unique and references to the table's other cells keep working."""
    notes: list[Note] = []
    masked_text = "[скрыто: похоже на указание для модели]"
    out = []
    for table in tables:
        updates: dict[str, object] = {}
        if reason := instruction_reason(table.title):
            notes.append(_note(table.source, "заголовок таблицы", reason, table.title))
            updates["title"] = masked_text
        if reason := instruction_reason(table.row_header):
            notes.append(_note(table.source, "заголовок первого столбца", reason, table.row_header))
            updates["row_header"] = masked_text
        columns: list[Column] = []
        for i, column in enumerate(table.columns, start=1):
            if reason := instruction_reason(column.label):
                notes.append(_note(table.source, f"столбец {i}", reason, column.label))
                column = column.model_copy(update={"label": f"[скрыт столбец {i}]"})
            columns.append(column)
        updates["columns"] = columns
        rows: list[Row] = []
        for n, row in enumerate(table.rows, start=1):
            label = row.label
            if reason := instruction_reason(row.label):
                notes.append(_note(table.source, f"строка {n}", reason, row.label))
                label = f"[скрыта строка {n}]"
            cells: list[Cell] = []
            for column, cell in zip(columns, row.cells, strict=True):
                if cell.text and (reason := instruction_reason(cell.text)):
                    notes.append(
                        _note(table.source, f"{label} › {column.label}", reason, cell.text)
                    )
                    cell = cell.model_copy(update={"text": masked_text})
                cells.append(cell)
            rows.append(row.model_copy(update={"label": label, "cells": cells}))
        updates["rows"] = rows
        out.append(table.model_copy(update=updates))
    return out, notes
