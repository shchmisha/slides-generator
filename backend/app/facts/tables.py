"""Raw table → cleaned table: header, row labels, numbers, units, and checks of the totals."""

import re
from typing import Literal

from app.facts.format import format_number
from app.facts.models import Cell, Column, Row, Table
from app.facts.numbers import parse_cell
from app.ingest.models import Note, RawCell, RawTable

_UNITS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"млрд\.?\s*(руб|₽)", re.I), "млрд ₽"),
    (re.compile(r"млн\.?\s*(руб|₽)", re.I), "млн ₽"),
    (re.compile(r"тыс\.?\s*(руб|₽)", re.I), "тыс. ₽"),
    (re.compile(r"(руб|₽)", re.I), "₽"),
    (re.compile(r"п\.\s?п\.", re.I), "п.п."),
    (re.compile(r"%"), "%"),
    (re.compile(r"\bшт\b", re.I), "шт."),
    (re.compile(r"\bчел\b", re.I), "чел."),
]
_TOTAL = re.compile(r"^(итого|всего|total)\b", re.I)
# Columns whose values are not additive, so sums over them are not checked.
_NOT_ADDITIVE = re.compile(r"средн|доля|%|nps|индекс|рейтинг|выполнени", re.I)


def unit_from_label(label: str) -> str | None:
    """Unit from the last comma-separated part: "Доставка в срок, %" → "%"."""
    parts = label.rsplit(",", 1)
    if len(parts) < 2:
        return None
    tail = parts[1]
    return next((unit for pattern, unit in _UNITS if pattern.search(tail)), None)


def _label(cell: RawCell, fallback: str) -> str:
    if cell.value is None:
        return fallback
    if isinstance(cell.value, float) and cell.value.is_integer():
        return str(int(cell.value))
    return " ".join(str(cell.value).split())


def _unique(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for label in labels:
        key = label.casefold()
        seen[key] = seen.get(key, 0) + 1
        result.append(label if seen[key] == 1 else f"{label} ({seen[key]})")
    return result


def normalize(raw: RawTable, table_id: str) -> tuple[Table, list[Note]]:
    header, *body = raw.rows
    title = raw.title or f"Таблица {table_id}"
    labels = _unique([_label(c, f"Столбец {i}") for i, c in enumerate(header[1:], start=2)])
    parsed = [[parse_cell(c.value) for c in row[1:]] for row in body]
    numeric = [
        sum(p[i].kind == "number" for p in parsed) > sum(p[i].kind == "text" for p in parsed)
        for i in range(len(labels))
    ]
    columns = [
        Column(label=label, unit=unit_from_label(label), numeric=is_numeric)
        for label, is_numeric in zip(labels, numeric, strict=True)
    ]
    row_labels = _unique([_label(row[0], f"Строка {n}") for n, row in enumerate(body, start=1)])

    notes: list[Note] = []
    rows: list[Row] = []
    for row_label, raw_row, parsed_row in zip(row_labels, body, parsed, strict=True):
        cells: list[Cell] = []
        for column, raw_cell, p in zip(columns, raw_row[1:], parsed_row, strict=True):
            location = f"{row_label} › {column.label}" + (
                f" ({raw_cell.address})" if raw_cell.address else ""
            )
            if not column.numeric:
                cells.append(Cell(text=str(raw_cell.value) if raw_cell.value is not None else None,
                                  address=raw_cell.address))  # fmt: skip
                continue
            if p.kind == "number":
                cells.append(Cell(value=p.value, decimals=p.decimals, address=raw_cell.address))
                if p.note:
                    notes.append(_note("info", raw, location, f"Исправлено: {p.note}"))
            elif p.kind in ("missing", "empty"):
                cells.append(Cell(missing=True, address=raw_cell.address))
                why = p.note or "пустая ячейка — значения нет, в расчётах не используется"
                notes.append(_note("warning", raw, location, f"Нет данных: {why}"))
            else:
                cells.append(Cell(missing=True, text=str(raw_cell.value), address=raw_cell.address))
                notes.append(
                    _note(
                        "warning", raw, location, f"Не число: «{_echo(raw_cell.value)}» — пропущено"
                    )
                )
        rows.append(Row(label=row_label, cells=cells, is_total=bool(_TOTAL.match(row_label))))

    table = Table(
        id=table_id,
        title=title,
        source=raw.source,
        unit=unit_from_label(title),
        row_header=_label(header[0], "Показатель"),
        columns=columns,
        rows=rows,
    )
    return table, notes + check_totals(table)


def _echo(value: object, limit: int = 40) -> str:
    """Cell text quoted back in a note, kept short: notes are shown to the model too."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _note(severity: Literal["info", "warning"], raw: RawTable, location: str, message: str) -> Note:
    return Note(severity=severity, source=raw.source, location=location, message=message)


def _additive(table: Table, column: Column) -> bool:
    return (
        column.numeric and table.unit_of(column) != "%" and not _NOT_ADDITIVE.search(column.label)
    )


def _close(expected: float, actual: float, decimals: int) -> bool:
    return abs(expected - actual) <= 0.51 * 10.0 ** (-decimals)


def check_totals(table: Table) -> list[Note]:
    """Compare "Итого" rows and columns with the sums they should equal."""
    notes: list[Note] = []
    checked = 0
    cols = list(enumerate(table.columns))

    def mismatch(where: str, actual: float, expected: float, decimals: int) -> None:
        notes.append(
            Note(
                severity="warning",
                source=table.source,
                location=where,
                message=f"Итог не сходится: в таблице {format_number(actual, decimals)}, "
                f"а сумма составляющих {format_number(expected, decimals)}",
            )
        )

    group: list[Row] = []
    for row in table.rows:
        if not row.is_total:
            group.append(row)
            continue
        for i, column in cols:
            values = [r.cells[i] for r in group]
            total = row.cells[i]
            if not _additive(table, column) or total.value is None or not values:
                continue
            if any(v.value is None for v in values):
                continue
            decimals = max(c.decimals for c in [*values, total])
            expected = sum(v.value for v in values if v.value is not None)
            checked += 1
            if not _close(expected, total.value, decimals):
                mismatch(f"{row.label} › {column.label}", total.value, expected, decimals)
        group = []

    for i, column in cols:
        match = _TOTAL.match(column.label)
        if not match or not _additive(table, column):
            continue
        suffix = column.label[match.end() :].strip().casefold()
        parts = [
            j
            for j, other in cols
            if j != i
            and _additive(table, other)
            and not _TOTAL.match(other.label)
            and (not suffix or other.label.casefold().endswith(" " + suffix))
        ]
        if not parts:
            continue
        for row in table.rows:
            total = row.cells[i]
            values = [row.cells[j] for j in parts]
            if total.value is None or any(v.value is None for v in values):
                continue
            decimals = max(c.decimals for c in [*values, total])
            expected = sum(v.value for v in values if v.value is not None)
            checked += 1
            if not _close(expected, total.value, decimals):
                mismatch(f"{row.label} › {column.label}", total.value, expected, decimals)

    if checked and not notes:
        notes.append(
            Note(
                severity="info",
                source=table.source,
                message="Проверено: итоговые строки и столбцы сходятся с суммами "
                f"({checked} проверок)",
            )
        )
    return notes
