"""Wording checks: a few reliable rules for words that contradict the data.

Numbers are already guaranteed by placeholders; these rules catch the commonest wrong words
around them, found in live runs:
- a level used as a gap: "отстал от плана на {completion}" where completion is 96,6 %
  (the gap is 3,4 % or 16 млн ₽);
- trend words about single-period data: "жалобы растут" when the table has one quarter only;
- the slide count asked for in the materials.

Findings are soft: they are sent back to the model once, and never fail a generation (a rule
of thumb is not worth a broken deck). Each message says what is wrong and how to say it.
"""

from __future__ import annotations

import re

from app.facts.digest import is_single_period
from app.facts.models import CellRef, ColumnRef, NameRef, Operation, RowRef, Table
from app.scenario import deck as sc
from app.scenario.text import PLACEHOLDER

_LEVEL_OPS = {"percent_of", "share"}
_GAP_WORDS = re.compile(
    r"(отстал\w*|отставани\w*|недобор\w*|недовыполн\w*|превысил\w*|превышени\w*|перевыполн\w*"
    r"|ниже плана|выше плана|не хватило|упал\w*|снизил\w*|вырос\w*)"
    r"(?:\s+[^\s{]+){0,3}?\s+на\s+\{([a-z][a-z0-9_]*)",
    re.IGNORECASE,
)
_TREND = re.compile(
    r"\b(раст[её]т|растут|рост\w*|вырос\w*|снижа\w*|сниз\w*|пада\w*|упал\w*|ухудш\w*|улучш\w*"
    r"|деград\w*|просел\w*|просед\w*|просадк\w*|увеличи\w*|уменьши\w*|динамик\w*"
    r"|стабильн\w*|сохраня\w*|по-прежнему|оста[её]тся|восстанов\w*|вернул\w*)",
    re.IGNORECASE,
)
# A sentence comparing with something explicit is not a trend claim about one period.
_COMPARED = re.compile(
    r"к прошлому|прошлого года|к плану|по сравнению|относительно|чем в "
    r"|к (?:январ|феврал|март|апрел|ма[юя]|июн|июл|август|сентябр|октябр|ноябр|декабр)",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?…;])\s+")


def _tables_of(ref: object, lineage: dict[str, set[str]]) -> set[str]:
    if isinstance(ref, CellRef | RowRef | ColumnRef):
        return {ref.table}
    if isinstance(ref, NameRef):
        return lineage.get(ref.name, set())
    return set()


def _operation_inputs(op: Operation) -> list[object]:
    return [
        getattr(op, field)
        for field in ("a", "b", "part", "whole", "new", "old", "of")
        if hasattr(op, field)
    ]


def _table_lineage(scenario: sc.DeckScenario) -> dict[str, set[str]]:
    lineage: dict[str, set[str]] = {}
    for name, ref in scenario.values.items():
        lineage[name] = _tables_of(ref, lineage)
    for op in scenario.derived:
        tables: set[str] = set()
        for value in _operation_inputs(op):
            for item in value if isinstance(value, list) else [value]:
                tables |= _tables_of(item, lineage)
        lineage[op.id] = tables
    return lineage


def _texts(scenario: sc.DeckScenario) -> list[tuple[str, str]]:
    """(path, text) for every free text of the slides."""
    out: list[tuple[str, str]] = []
    for i, slide in enumerate(scenario.slides):
        base = f"slides[{i}]"
        for field in ("title", "subtitle", "callout"):
            value = getattr(slide, field, None)
            if isinstance(value, str):
                out.append((f"{base}.{field}", value))
        if isinstance(slide, sc.KeyTakeaways):
            out += [(f"{base}.takeaways[{j}]", t) for j, t in enumerate(slide.takeaways)]
            out += [
                (f"{base}.kpis[{j}].delta", k.delta) for j, k in enumerate(slide.kpis) if k.delta
            ]
        if isinstance(slide, sc.RisksDecisions):
            out += [(f"{base}.risks[{j}].text", p.text) for j, p in enumerate(slide.risks)]
            out += [(f"{base}.risks[{j}].title", p.title) for j, p in enumerate(slide.risks)]
    return out


def lint_deck(
    scenario: sc.DeckScenario,
    tables: list[Table],
    slide_count: int,
    wanted: tuple[int, int] | None,
) -> list[str]:
    findings: list[str] = []
    if wanted is not None and not wanted[0] <= slide_count <= wanted[1]:
        low, high = wanted
        need = f"{low}" if low == high else f"{low}–{high}"
        findings.append(
            f"slides: в материалах просят {need} слайдов, а их {slide_count} "
            "(слайд «Проверка данных» входит в число)"
        )

    ops = {op.id: op.op for op in scenario.derived}
    lineage = _table_lineage(scenario)
    single = {t.id for t in tables if is_single_period(t)}
    single_stems = {
        word[:5]
        for t in tables
        if t.id in single
        for column in t.columns
        for word in re.findall(r"[а-яёa-z]{5,}", column.label.casefold())
    }

    for path, text in _texts(scenario):
        for match in _GAP_WORDS.finditer(text):
            name = match.group(2)
            if ops.get(name) in _LEVEL_OPS:
                findings.append(
                    f"{path}: «{match.group(0)}}}» — {{{name}}} это уровень (например, выполнение "
                    "плана 96,6 %), а не разница. Пиши «выполнил план на {…}», а разницу считай "
                    "через difference («недобор {…}») или percent_change"
                )
        for sentence in _SENTENCE.split(text):
            if not _TREND.search(sentence) or _COMPARED.search(sentence):
                continue
            names = [m.group(1) for m in PLACEHOLDER.finditer(sentence)]
            sources = set().union(*(lineage.get(n, set()) for n in names)) if names else set()
            if names and sources and sources <= single:
                bad = True
            elif not names:
                words = {w[:5] for w in re.findall(r"[а-яёa-z]{5,}", sentence.casefold())}
                bad = bool(words & single_stems)
            else:
                bad = False
            if bad:
                findings.append(
                    f"{path}: «{sentence.strip()}» — по этим показателям в данных один период, "
                    "динамики нет. Сравни строки между собой («хуже всех», «ниже среднего») "
                    "вместо слов «растёт», «снижается», «ухудшается»"
                )
    return findings
