"""Cleaned tables, references to their values, and calculated (derived) facts.

The model never types a number: it points at a value with a reference and, when a figure is
missing from the source, asks for an operation over references. Everything here serialises to
JSON, so it can be stored with the project and shown to the model and to the user.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.facts.format import format_quantity


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ------------------------------------------------------------------------- cleaned tables


class Column(_Frozen):
    label: str
    unit: str | None = None
    numeric: bool = False


class Cell(_Frozen):
    value: float | None = None  # set only for numbers
    decimals: int = 0
    missing: bool = False  # a placeholder or an empty cell in a numeric column
    text: str | None = None  # set only for non-numeric content
    address: str | None = None


class Row(_Frozen):
    label: str
    cells: list[Cell]  # one per column, the label column excluded
    is_total: bool = False


class Table(_Frozen):
    id: str  # "t1", "t2" … in upload order
    title: str
    source: str  # "q3_metrics.xlsx › Выручка"
    unit: str | None = None  # from the title: "млн ₽"
    row_header: str  # label of the first column: "Филиал"
    columns: list[Column]
    rows: list[Row]

    def unit_of(self, column: Column) -> str | None:
        return column.unit or self.unit


# ------------------------------------------------------------------------------ references


class CellRef(_Frozen):
    """One value: {"table": "t1", "row": "Урал", "col": "Итого факт"}."""

    table: str
    row: str
    col: str


class RowRef(_Frozen):
    """A row as a series over its columns (all numeric columns when `cols` is omitted)."""

    table: str
    row: str
    cols: list[str] | None = None


class ColumnRef(_Frozen):
    """A column as a series over its rows (all rows except totals when `rows` is omitted)."""

    table: str
    col: str
    rows: list[str] | None = None


class DerivedRef(_Frozen):
    """The result of an earlier operation: {"derived": "profit"}."""

    derived: str


Ref = CellRef | RowRef | ColumnRef | DerivedRef


# ------------------------------------------------------------------------------ quantities


class Quantity(_Frozen):
    value: float | None
    unit: str | None
    decimals: int
    label: str  # "Выручка по филиалам › Урал › Итого факт"
    origin: str  # "q3_metrics.xlsx › Выручка!I6" or the formula of a derived value

    def formatted(self, *, signed: bool = False) -> str:
        return format_quantity(self.value, self.unit, self.decimals, signed=signed)


class SeriesItem(_Frozen):
    key: str  # row or column label: "Урал", "Июль факт"
    quantity: Quantity


class Series(_Frozen):
    label: str
    unit: str | None
    items: list[SeriesItem]


# ------------------------------------------------------------------------------ operations

DerivedId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
Decimals = Annotated[int, Field(ge=0, le=4)]


class _Op(_Frozen):
    id: DerivedId
    label: str = Field(min_length=1, max_length=200)
    decimals: Decimals | None = None


class SumOp(_Op):
    """Sum of values; of one series → its total; of several series → element by element."""

    op: Literal["sum"]
    of: list[Ref] = Field(min_length=1)


class DifferenceOp(_Op):
    """a \u2212 b. The difference of two percentages is in percentage points."""

    op: Literal["difference"]
    a: Ref
    b: Ref


class RatioOp(_Op):
    """a / b, optionally with a unit for the result (e.g. "₽ на заказ")."""

    op: Literal["ratio"]
    a: Ref
    b: Ref
    unit: str | None = Field(default=None, max_length=30)


class PercentOfOp(_Op):
    """part / whole × 100 — e.g. plan completion: fact as % of plan."""

    op: Literal["percent_of"]
    part: Ref
    whole: Ref


class PercentChangeOp(_Op):
    """(new \u2212 old) / old × 100 — growth vs last year or last month."""

    op: Literal["percent_change"]
    new: Ref
    old: Ref


class ShareOp(_Op):
    """part / whole × 100; with no whole, each item of a series as a share of the series total."""

    op: Literal["share"]
    part: Ref
    whole: Ref | None = None


class ScaleOp(_Op):
    """a × factor — e.g. complaints per 1 000 orders. The factor is a constant, not data."""

    op: Literal["scale"]
    a: Ref
    factor: float = Field(gt=0, le=1_000_000)
    unit: str | None = Field(default=None, max_length=30)


Operation = Annotated[
    SumOp | DifferenceOp | RatioOp | PercentOfOp | PercentChangeOp | ShareOp | ScaleOp,
    Field(discriminator="op"),
]


class Derived(_Frozen):
    id: str
    label: str
    op: str
    result: Quantity | Series
    warnings: list[str] = Field(default_factory=list)
