"""Calculations over facts, checked against figures computed by hand from q3_metrics.xlsx."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.facts.build import build_facts
from app.facts.models import Operation, Quantity, Series
from app.facts.ops import evaluate
from app.facts.store import FactError, FactStore
from app.ingest.extract import extract_file

FIXTURES = Path(__file__).parent / "fixtures"
OPERATION = TypeAdapter(Operation)


@pytest.fixture
def store() -> FactStore:
    document = extract_file((FIXTURES / "q3_metrics.xlsx").read_bytes(), "q3_metrics.xlsx")
    return build_facts([document])[0]


def run(store: FactStore, spec: dict[str, Any]) -> Quantity | Series:
    """Operations arrive as JSON from the agent, so tests build them the same way."""
    return evaluate(OPERATION.validate_python(spec), store).result


def cell(table: str, row: str, col: str) -> dict[str, str]:
    return {"table": table, "row": row, "col": col}


def test_growth_vs_last_year_is_8_2_not_12(store: FactStore) -> None:
    result = run(store, {
        "id": "yoy", "op": "percent_change", "label": "Рост к 3 кв. прошлого года",
        "new": cell("t1", "Итого по компании", "Итого факт"),
        "old": cell("t4", "Итого по компании", "Итого"),
    })  # fmt: skip
    assert isinstance(result, Quantity)
    assert result.value == pytest.approx(8.158, abs=1e-3)
    assert result.formatted() == "8,2\u00a0%"
    assert result.origin == "(2\u00a0877 \u2212 2\u00a0660) / 2\u00a0660 × 100 = 8,2\u00a0%"


def test_plan_completion_by_region(store: FactStore) -> None:
    result = run(store, {
        "id": "completion", "op": "percent_of", "label": "Выполнение плана",
        "part": {"table": "t1", "col": "Итого факт"},
        "whole": {"table": "t1", "col": "Итого план"},
    })  # fmt: skip
    assert isinstance(result, Series)
    shown = {i.key: i.quantity.formatted() for i in result.items}
    assert shown["Урал"] == "96,6\u00a0%"
    assert shown["Юг"] == "104,5\u00a0%"


def test_monthly_profit_and_margin_chain_across_tables(store: FactStore) -> None:
    profit = run(store, {
        "id": "profit", "op": "difference", "label": "Прибыль",
        "a": {"table": "t1", "row": "Итого по компании",
              "cols": ["Июль факт", "Август факт", "Сентябрь факт"]},
        "b": {"table": "t2", "row": "Итого затрат", "cols": ["Июль", "Август", "Сентябрь"]},
    })  # fmt: skip
    assert isinstance(profit, Series)
    assert [i.key for i in profit.items] == ["Июль", "Август", "Сентябрь"]
    assert [i.quantity.value for i in profit.items] == [124, 105, 133]
    assert profit.unit == "млн ₽"

    margin = run(store, {
        "id": "margin", "op": "percent_of", "label": "Маржа",
        "part": {"name": "profit"},
        "whole": {"table": "t1", "row": "Итого по компании",
                  "cols": ["Июль факт", "Август факт", "Сентябрь факт"]},
    })  # fmt: skip
    assert isinstance(margin, Series)
    assert [i.quantity.formatted() for i in margin.items] == [
        "13,2\u00a0%",
        "11,1\u00a0%",
        "13,3\u00a0%",
    ]


def test_cost_structure_shares_sum_to_100(store: FactStore) -> None:
    result = run(store, {
        "id": "cost_share", "op": "share", "label": "Доля в затратах",
        "part": {"table": "t2", "col": "Итого"},
    })  # fmt: skip
    assert isinstance(result, Series)
    assert sum(i.quantity.value or 0 for i in result.items) == pytest.approx(100)
    assert {i.key: i.quantity.formatted() for i in result.items}["ФОТ"] == "45,6\u00a0%"


def test_marketing_growth_within_quarter(store: FactStore) -> None:
    result = run(store, {
        "id": "marketing", "op": "percent_change", "label": "Маркетинг: сентябрь к июлю",
        "new": cell("t2", "Маркетинг", "Сентябрь"), "old": cell("t2", "Маркетинг", "Июль"),
    })  # fmt: skip
    assert isinstance(result, Quantity)
    assert result.formatted() == "55,6\u00a0%"


def test_complaints_per_thousand_orders(store: FactStore) -> None:
    run(store, {
        "id": "complaint_rate", "op": "ratio", "label": "Жалоб на заказ",
        "a": {"table": "t3", "col": "Жалоб, шт."}, "b": {"table": "t3", "col": "Заказов, шт."},
    })  # fmt: skip
    result = run(store, {
        "id": "per_1000", "op": "scale", "label": "Жалоб на 1000 заказов", "factor": 1000,
        "a": {"name": "complaint_rate"}, "unit": "на 1000 заказов",
    })  # fmt: skip
    assert isinstance(result, Series)
    assert {i.key: i.quantity.formatted() for i in result.items}["Юг"] == "9,2\u00a0на 1000 заказов"


def test_difference_of_percentages_is_in_points(store: FactStore) -> None:
    result = run(store, {
        "id": "gap", "op": "difference", "label": "Отставание Сибири по доставке",
        "a": cell("t3", "Сибирь", "Доставка в срок, %"),
        "b": cell("t3", "Центр", "Доставка в срок, %"),
    })  # fmt: skip
    assert isinstance(result, Quantity)
    assert result.formatted() == "\u22128,4\u00a0п.п."


def test_missing_input_gives_missing_result_and_warning(store: FactStore) -> None:
    derived = evaluate(OPERATION.validate_python({
        "id": "nps_gap", "op": "difference", "label": "NPS: Сибирь против Центра",
        "a": cell("t3", "Сибирь", "NPS"), "b": cell("t3", "Центр", "NPS"),
    }), store)  # fmt: skip
    assert isinstance(derived.result, Quantity)
    assert derived.result.value is None
    assert derived.result.formatted() == "нет данных"
    assert "нет данных" in derived.warnings[0]


def test_division_by_zero_is_a_warning_not_a_crash(store: FactStore) -> None:
    plan = cell("t1", "Север", "Итого план")
    run(store, {"id": "zero", "op": "difference", "label": "Ноль", "a": plan, "b": plan})
    derived = evaluate(OPERATION.validate_python({
        "id": "ratio_zero", "op": "ratio", "label": "Деление на ноль",
        "a": cell("t1", "Север", "Итого факт"), "b": {"name": "zero"},
    }), store)  # fmt: skip
    assert derived.result.value is None  # type: ignore[union-attr]
    assert "деление на ноль" in derived.warnings[0]


def test_unknown_reference_and_duplicate_id_are_errors(store: FactStore) -> None:
    with pytest.raises(FactError, match="нет строки"):
        run(
            store,
            {"id": "x", "op": "sum", "label": "x", "of": [cell("t1", "Москва", "Итого факт")]},
        )
    run(
        store,
        {
            "id": "total",
            "op": "sum",
            "label": "Итого",
            "of": [{"table": "t1", "col": "Итого факт"}],
        },
    )
    with pytest.raises(FactError, match="уже занято"):
        run(
            store,
            {
                "id": "total",
                "op": "sum",
                "label": "Итого",
                "of": [{"table": "t1", "col": "Итого факт"}],
            },
        )


def test_sum_of_series_matches_the_total_row(store: FactStore) -> None:
    result = run(store, {"id": "sum_fact", "op": "sum", "label": "Сумма по филиалам",
                         "of": [{"table": "t1", "col": "Итого факт"}]})  # fmt: skip
    assert isinstance(result, Quantity)
    assert result.value == 2877


@pytest.mark.parametrize(
    "spec",
    [
        {"id": "Bad Id", "op": "sum", "label": "x", "of": [{"name": "a"}]},
        {"id": "x", "op": "eval", "label": "x", "code": "__import__('os')"},
        {"id": "x", "op": "scale", "label": "x", "a": {"name": "a"}, "factor": -1},
        {
            "id": "x",
            "op": "sum",
            "label": "x",
            "of": [{"table": "t1", "row": "Урал", "col": "x", "sql": "1"}],
        },
    ],
)
def test_operations_are_a_closed_set(spec: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OPERATION.validate_python(spec)


def test_rate_per_thousand(store: FactStore) -> None:
    result = run(store, {
        "id": "rate", "op": "rate", "label": "Жалоб на 1000 заказов", "per": 1000,
        "a": {"table": "t3", "col": "Жалоб, шт."}, "b": {"table": "t3", "col": "Заказов, шт."},
    })  # fmt: skip
    assert isinstance(result, Series)
    siberia = next(i.quantity for i in result.items if i.key == "Сибирь")
    assert siberia.formatted() == "13,9"
    assert siberia.origin == "470 / 33 900 × 1 000 = 13,9"


def test_mean_skips_missing_values_with_a_warning(store: FactStore) -> None:
    derived = evaluate(OPERATION.validate_python({
        "id": "nps_mean", "op": "mean", "label": "Средний NPS", "of": {"table": "t3", "col": "NPS"},
    }), store)  # fmt: skip
    assert isinstance(derived.result, Quantity)
    assert derived.result.formatted() == "50,3"  # (52 + 47 + 58 + 44) / 4 = 50,25
    assert "Сибирь" in derived.warnings[0]


@pytest.mark.parametrize("factor", [3.4755, 2, 0.5, 999])
def test_scale_accepts_only_powers_of_ten(factor: float) -> None:
    with pytest.raises(ValidationError, match="перевод единиц"):
        OPERATION.validate_python(
            {"id": "x", "op": "scale", "label": "x", "a": {"name": "a"}, "factor": factor}
        )
