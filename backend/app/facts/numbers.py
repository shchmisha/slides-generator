"""Reading one cell: a number, a "no data" placeholder, empty, or plain text.

Live spreadsheets mix formats ("224,0" stored as text, "нет данных"); every conversion that is
not a plain number is described in `note` so the user can see what was fixed.
"""

import math
import re
from dataclasses import dataclass
from typing import Literal

from app.facts.format import format_number
from app.ingest.models import RawValue

MISSING_MARKERS = frozenset(
    {
        "-",
        "—",
        "–",
        "н/д",
        "н.д.",
        "нет",
        "нет данных",
        "n/a",
        "na",
        "#н/д",
        "#n/a",
        "#div/0!",
        "#дел/0!",
        "#value!",
        "#знач!",
    }
)
# Decimal comma or point; optional thousands groups separated by (no-break) spaces; optional %.
_NUMBER = re.compile(
    r"^(?P<sign>[+\-\u2212–]?)\s*"
    r"(?P<int>\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)"
    r"(?:[.,](?P<frac>\d+))?"
    r"\s*(?P<pct>%?)$"
)
MAX_DECIMALS = 4


@dataclass(frozen=True)
class ParsedCell:
    kind: Literal["number", "missing", "empty", "text"]
    value: float | None = None
    decimals: int = 0
    percent: bool = False  # the source carried a "%" sign
    note: str | None = None  # what was fixed, for the data report


def _decimals(fraction: str) -> int:
    return min(len(fraction.rstrip("0")), MAX_DECIMALS)


def _float_decimals(value: float) -> int:
    text = repr(value)
    if "e" in text or "E" in text or "." not in text:
        return 0
    return _decimals(text.split(".")[1])


def parse_cell(raw: RawValue) -> ParsedCell:
    if raw is None:
        return ParsedCell("empty")
    if isinstance(raw, bool):
        return ParsedCell("text")
    if isinstance(raw, int):
        return ParsedCell("number", float(raw))
    if isinstance(raw, float):
        if not math.isfinite(raw):
            return ParsedCell("missing", note="значение не является числом")
        return ParsedCell("number", raw, _float_decimals(raw))

    text = raw.strip()
    if not text:
        return ParsedCell("empty")
    if text.casefold() in MISSING_MARKERS:
        return ParsedCell("missing", note=f"«{text}» — значения нет, в расчётах не используется")
    match = _NUMBER.match(text)
    if match is None:
        return ParsedCell("text")
    integer = re.sub(r"\D", "", match["int"])
    fraction = match["frac"] or ""
    value = float(f"{integer}.{fraction or 0}")
    if match["sign"] in ("-", "\u2212", "–"):
        value = -value
    decimals = _decimals(fraction)
    percent = bool(match["pct"])
    shown = format_number(value, decimals) + (" %" if percent else "")
    return ParsedCell(
        "number",
        value,
        decimals,
        percent,
        note=f"число записано текстом «{text}» — прочитано как {shown}",
    )
