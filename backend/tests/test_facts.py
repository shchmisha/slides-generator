"""Cleaning, totals checks and fact references on the client's workbook."""

from pathlib import Path

import pytest

from app.facts.build import build_facts
from app.facts.format import format_number, format_quantity
from app.facts.models import CellRef, ColumnRef, RowRef
from app.facts.numbers import parse_cell
from app.facts.store import FactError, FactStore
from app.facts.tables import normalize, unit_from_label
from app.ingest.extract import extract_file
from app.ingest.models import Note, RawCell, RawTable

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client_facts() -> tuple[FactStore, list[Note]]:
    document = extract_file((FIXTURES / "q3_metrics.xlsx").read_bytes(), "q3_metrics.xlsx")
    return build_facts([document])


@pytest.mark.parametrize(
    ("raw", "kind", "value"),
    [
        (224, "number", 224),
        ("224,0", "number", 224),
        ("1 050", "number", 1050),
        ("1\u00a0050,5", "number", 1050.5),
        ("\u221216", "number", -16),
        ("12%", "number", 12),
        ("нет данных", "missing", None),
        ("Н/Д", "missing", None),
        ("-", "missing", None),
        (None, "empty", None),
        ("Север", "text", None),
        (float("nan"), "missing", None),
    ],
)
def test_parse_cell(raw: object, kind: str, value: float | None) -> None:
    parsed = parse_cell(raw)  # type: ignore[arg-type]
    assert parsed.kind == kind
    assert parsed.value == value


def test_number_stored_as_text_is_fixed_and_reported(
    client_facts: tuple[FactStore, list[Note]],
) -> None:
    store, notes = client_facts
    yug = store.resolve(CellRef(table="t1", row="Юг", col="Июль факт"))
    assert yug.value == 224  # type: ignore[union-attr]
    assert yug.origin == "q3_metrics.xlsx › Выручка!C5"  # type: ignore[union-attr]
    fixed = [n for n in notes if n.location and n.location.startswith("Юг › Июль факт")]
    assert fixed[0].severity == "info"
    assert "«224,0»" in fixed[0].message


def test_missing_value_is_reported_and_stays_missing(
    client_facts: tuple[FactStore, list[Note]],
) -> None:
    store, notes = client_facts
    nps = store.resolve(CellRef(table="t3", row="Сибирь", col="NPS"))
    assert nps.value is None  # type: ignore[union-attr]
    warning = next(n for n in notes if n.location and n.location.startswith("Сибирь › NPS"))
    assert warning.severity == "warning"
    assert "нет данных" in warning.message


def test_client_totals_add_up(client_facts: tuple[FactStore, list[Note]]) -> None:
    _, notes = client_facts
    assert not [n for n in notes if "не сходится" in n.message]
    assert sum("сходятся" in n.message for n in notes) == 3  # revenue, costs, last year


def test_units_come_from_titles_and_headers(client_facts: tuple[FactStore, list[Note]]) -> None:
    store, _ = client_facts
    assert store.table("t1").unit == "млн ₽"
    on_time = store.resolve(CellRef(table="t3", row="Урал", col="Доставка в срок, %"))
    assert on_time.unit == "%"  # type: ignore[union-attr]
    assert unit_from_label("Средний чек, тыс. руб.") == "тыс. ₽"
    assert unit_from_label("NPS") is None


def test_broken_total_is_flagged() -> None:
    raw = RawTable(
        source="t.xlsx › Лист1",
        title="Выручка, млн руб.",
        rows=[
            [RawCell(value="Филиал"), RawCell(value="Факт")],
            [RawCell(value="Север"), RawCell(value=100)],
            [RawCell(value="Юг"), RawCell(value=200)],
            [RawCell(value="Итого"), RawCell(value=310, address="B4")],
        ],
    )
    _, notes = normalize(raw, "t1")
    mismatch = next(n for n in notes if "не сходится" in n.message)
    assert mismatch.location == "Итого › Факт"
    assert "310" in mismatch.message and "300" in mismatch.message


def test_series_references(client_facts: tuple[FactStore, list[Note]]) -> None:
    store, _ = client_facts
    by_region = store.resolve(ColumnRef(table="t1", col="Итого факт"))
    assert [i.key for i in by_region.items] == ["Север", "Юг", "Центр", "Урал", "Сибирь"]  # type: ignore[union-attr]
    months = store.resolve(RowRef(table="t2", row="Маркетинг", cols=["Июль", "Сентябрь"]))
    assert [i.quantity.value for i in months.items] == [45, 70]  # type: ignore[union-attr]


def test_labels_match_loosely_and_errors_suggest_alternatives(
    client_facts: tuple[FactStore, list[Note]],
) -> None:
    store, _ = client_facts
    q = store.resolve(CellRef(table="t1", row="  урал ", col="итого  ФАКТ"))
    assert q.value == 449  # type: ignore[union-attr]
    with pytest.raises(FactError, match="Урал"):
        store.resolve(CellRef(table="t1", row="Уралл", col="Итого факт"))
    with pytest.raises(FactError, match="Нет таблицы"):
        store.resolve(CellRef(table="t9", row="Урал", col="Итого факт"))


def test_russian_formatting() -> None:
    assert format_number(2877) == "2\u00a0877"
    assert format_number(8.234, 1) == "8,2"
    assert format_number(-16, signed=True) == "\u221216"
    assert format_number(29, signed=True) == "+29"
    assert format_number(-0.04, 1) == "0,0"
    assert format_quantity(2877, "млн ₽") == "2\u00a0877\u00a0млн ₽"
    assert format_quantity(None, "%") == "нет данных"
