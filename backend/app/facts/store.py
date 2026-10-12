"""All facts of a project: cleaned tables plus derived values, resolved by reference.

Errors name the closest existing labels, so the agent can correct a reference in one retry.
"""

from __future__ import annotations

import difflib

from app.facts.models import (
    CellRef,
    Column,
    ColumnRef,
    Derived,
    DerivedRef,
    Quantity,
    Ref,
    Row,
    RowRef,
    Series,
    SeriesItem,
    Table,
)


class FactError(ValueError):
    """A reference or operation cannot be resolved; the message is meant for the agent."""


def normalize_label(label: str) -> str:
    return " ".join(label.casefold().replace("ё", "е").split())


def _suggest(wanted: str, options: list[str]) -> str:
    close = difflib.get_close_matches(wanted, options, n=3, cutoff=0.5)
    shown = close or options[:12]
    return ", ".join(f"«{o}»" for o in shown)


class FactStore:
    def __init__(self, tables: list[Table]) -> None:
        self._tables = {t.id: t for t in tables}
        self._derived: dict[str, Derived] = {}

    @property
    def tables(self) -> list[Table]:
        return list(self._tables.values())

    @property
    def derived(self) -> list[Derived]:
        return list(self._derived.values())

    def add_derived(self, derived: Derived) -> None:
        if derived.id in self._derived:
            raise FactError(f"Расчёт «{derived.id}» уже есть: выберите другой id")
        self._derived[derived.id] = derived

    # ------------------------------------------------------------------------ lookup

    def table(self, table_id: str) -> Table:
        try:
            return self._tables[table_id]
        except KeyError:
            raise FactError(
                f"Нет таблицы «{table_id}». Есть: {', '.join(self._tables) or 'нет таблиц'}"
            ) from None

    def _row(self, table: Table, label: str) -> Row:
        wanted = normalize_label(label)
        for row in table.rows:
            if normalize_label(row.label) == wanted:
                return row
        options = [r.label for r in table.rows]
        raise FactError(
            f"В таблице {table.id} нет строки «{label}». Похожие: {_suggest(label, options)}"
        )

    def _column(self, table: Table, label: str) -> tuple[int, Column]:
        wanted = normalize_label(label)
        for i, column in enumerate(table.columns):
            if normalize_label(column.label) == wanted:
                return i, column
        options = [c.label for c in table.columns]
        raise FactError(
            f"В таблице {table.id} нет столбца «{label}». Похожие: {_suggest(label, options)}"
        )

    def _quantity(self, table: Table, row: Row, index: int, column: Column) -> Quantity:
        if not column.numeric:
            raise FactError(f"«{row.label} › {column.label}» в таблице {table.id} — не число")
        cell = row.cells[index]
        origin = table.source + (f"!{cell.address}" if cell.address else "")
        return Quantity(
            value=cell.value,
            unit=table.unit_of(column),
            decimals=cell.decimals,
            label=f"{table.title} › {row.label} › {column.label}",
            origin=origin,
        )

    # ----------------------------------------------------------------------- resolve

    def resolve(self, ref: Ref) -> Quantity | Series:
        match ref:
            case DerivedRef():
                try:
                    return self._derived[ref.derived].result
                except KeyError:
                    raise FactError(
                        f"Нет расчёта «{ref.derived}». Есть: {', '.join(self._derived) or 'нет'}"
                    ) from None
            case CellRef():
                table = self.table(ref.table)
                row = self._row(table, ref.row)
                index, column = self._column(table, ref.col)
                return self._quantity(table, row, index, column)
            case RowRef():
                table = self.table(ref.table)
                row = self._row(table, ref.row)
                columns = (
                    [self._column(table, label) for label in ref.cols]
                    if ref.cols is not None
                    else [(i, c) for i, c in enumerate(table.columns) if c.numeric]
                )
                items = [
                    SeriesItem(key=c.label, quantity=self._quantity(table, row, i, c))
                    for i, c in columns
                ]
                return Series(
                    label=f"{table.title} › {row.label}", unit=_common_unit(items), items=items
                )
            case ColumnRef():
                table = self.table(ref.table)
                index, column = self._column(table, ref.col)
                rows = (
                    [self._row(table, label) for label in ref.rows]
                    if ref.rows is not None
                    else [r for r in table.rows if not r.is_total]
                )
                items = [
                    SeriesItem(key=r.label, quantity=self._quantity(table, r, index, column))
                    for r in rows
                ]
                return Series(
                    label=f"{table.title} › {column.label}", unit=table.unit_of(column), items=items
                )


def _common_unit(items: list[SeriesItem]) -> str | None:
    units = {item.quantity.unit for item in items}
    return units.pop() if len(units) == 1 else None
