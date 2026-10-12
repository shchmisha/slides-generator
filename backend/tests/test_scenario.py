"""Deck scenario → render spec, on the client's workbook and the reference board scenario."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.facts.build import build_facts
from app.facts.store import FactStore
from app.ingest.extract import extract_file
from app.scenario import render as rd
from app.scenario.deck import DeckScenario
from app.scenario.resolve import ScenarioError, display_keys, excel_format, resolve_deck

FIXTURES = Path(__file__).parent / "fixtures"
NBSP = " "


@pytest.fixture(scope="module")
def store() -> FactStore:
    document = extract_file((FIXTURES / "q3_metrics.xlsx").read_bytes(), "q3_metrics.xlsx")
    return build_facts([document])[0]


@pytest.fixture
def board() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "board_scenario.json").read_text())
    return data


def resolve(store: FactStore, data: dict[str, Any]):  # type: ignore[no-untyped-def]
    return resolve_deck(DeckScenario.model_validate(data), store)


def errors_of(store: FactStore, data: dict[str, Any]) -> list[str]:
    with pytest.raises(ScenarioError) as info:
        resolve(store, data)
    return info.value.errors


def test_reference_board_scenario_resolves(store: FactStore, board: dict[str, Any]) -> None:
    result = resolve(store, board)
    assert len(result.spec.slides) == 9
    first = result.spec.slides[0]
    assert isinstance(first, rd.KeyTakeawaysSlide)
    assert [(k.value, k.unit) for k in first.kpis] == [
        (f"2{NBSP}877", "млн ₽"),
        ("8,2", "%"),
        ("362", "млн ₽"),
        ("96,6", "%"),
    ]
    expected = (
        f"План по выручке перевыполнен на 1,5{NBSP}%: "
        f"2{NBSP}877{NBSP}млн ₽ при плане 2{NBSP}835{NBSP}млн ₽"
    )
    assert first.takeaways[0].startswith(expected)


def test_table_cells_signs_tones_and_totals(store: FactStore, board: dict[str, Any]) -> None:
    table = resolve(store, board).spec.slides[1].table  # type: ignore[union-attr]
    rows = {row[0].text: row for row in table.rows}
    assert [c.text for c in rows["Урал"]] == ["Урал", "465", "449", "−16", "96,6"]
    assert rows["Урал"][3].tone == "negative"
    assert rows["Урал"][4].bold  # lowest completion is highlighted
    assert rows["Юг"][3].text == "+29"
    assert table.total_row
    assert [c.text for c in rows["Итого"]] == [
        "Итого",
        f"2{NBSP}835",
        f"2{NBSP}877",
        "+42",
        "101,5",
    ]


def test_series_from_different_tables_line_up(store: FactStore, board: dict[str, Any]) -> None:
    result = resolve(store, board)
    profit = result.spec.slides[4]
    assert isinstance(profit, rd.ChartTableSlide)
    assert profit.chart.categories == ["Июль", "Август", "Сентябрь"]
    margin = profit.chart.series[2]
    assert (margin.axis, margin.values) == ("secondary", [13.2, 11.1, 13.3])
    assert profit.chart.secondary_format == '0.0"%"'
    assert [row[0].text for row in profit.table.rows] == ["Июль", "Август", "Сентябрь", "Итого"]
    assert any("по порядку" in w for w in result.warnings)


def test_cost_structure_shares_sorted_with_change_tones(
    store: FactStore, board: dict[str, Any]
) -> None:
    slide = resolve(store, board).spec.slides[5]
    assert isinstance(slide, rd.CostStructureSlide)
    assert [i.label for i in slide.items] == ["ФОТ", "Топливо", "Аренда", "Прочее", "Маркетинг"]
    assert slide.items[0].share_text == f"45,6{NBSP}%"
    marketing = slide.items[-1]
    assert (marketing.change, marketing.change_tone) == (f"+55,6{NBSP}%", "negative")
    assert slide.items[0].change_tone == "neutral"  # +1,3 % is below the threshold
    assert slide.total is not None
    assert slide.total.value == f"2{NBSP}515{NBSP}млн ₽"


def test_missing_nps_is_shown_as_no_data(store: FactStore, board: dict[str, Any]) -> None:
    table = resolve(store, board).spec.slides[6].table  # type: ignore[union-attr]
    siberia = next(row for row in table.rows if row[0].text == "Сибирь")
    assert siberia[3].text == "нет данных"


def test_manager_claim_is_checked_against_the_table(
    store: FactStore, board: dict[str, Any]
) -> None:
    claim = resolve(store, board).claims[0]
    assert claim.matches is False
    assert claim.value == f"8,2{NBSP}%"


def test_typed_numbers_are_rejected(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][0]["takeaways"][0] = "Рост к прошлому году составил 12 %, выручка 2 877 млн ₽."
    errors = errors_of(store, board)
    assert any("slides[0].takeaways[0]" in e and "«877»" in e for e in errors)


def test_years_quarters_and_small_counts_are_allowed(
    store: FactStore, board: dict[str, Any]
) -> None:
    board["slides"][0]["takeaways"][0] = "В 3 квартале 2025 года 1 из 5 филиалов ниже плана."
    resolve(store, board)


def test_unknown_names_and_keys_list_alternatives(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][0]["takeaways"][1] = "Урал: {completion[Уралл]}, всего {revenue}."
    errors = errors_of(store, board)
    assert any("нет «Уралл»" in e and "Урал" in e for e in errors)
    assert any("Нет значения «revenue»" in e for e in errors)


def test_series_without_key_in_text_is_an_error(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][0]["takeaways"][1] = "Выполнение: {completion}."
    assert any("укажите элемент" in e for e in errors_of(store, board))


def test_bad_reference_in_values_names_the_closest_label(
    store: FactStore, board: dict[str, Any]
) -> None:
    board["values"]["rev_fact"] = {"table": "t1", "row": "Итого по компании", "col": "Итог факт"}
    errors = errors_of(store, board)
    assert any(e.startswith("values.rev_fact") and "Итого факт" in e for e in errors)


def test_scalar_where_a_series_is_needed(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][1]["chart"]["series"][0]["value"] = "rev_plan"
    assert any("нужен ряд" in e for e in errors_of(store, board))


def test_all_errors_are_reported_at_once(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][0]["takeaways"][0] = "Выручка 2 877."
    board["slides"][2]["callout"] = "{nothing}"
    assert len(errors_of(store, board)) >= 2


@pytest.mark.parametrize(
    "patch",
    [
        {"kind": "video"},
        {"slides": []},
        {"slides": [{"type": "html", "html": "<script>alert(1)</script>"}]},
        {"values": {"Bad Name": {"name": "x"}}},
        {"eval": "__import__('os')"},
    ],
)
def test_schema_rejects_anything_outside_the_contract(
    patch: dict[str, Any], board: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        DeckScenario.model_validate({**board, **patch})


def test_helpers() -> None:
    assert excel_format(0, "млн ₽") == "#,##0"
    assert excel_format(1, "%") == '0.0"%"'
    assert display_keys(["Июль факт", "Август факт"]) == ["Июль", "Август"]
    assert display_keys(["Север", "Юг"]) == ["Север", "Юг"]


def test_render_spec_serialises_for_the_skill(store: FactStore, board: dict[str, Any]) -> None:
    data = json.loads(resolve(store, board).spec.to_json())
    slide = data["slides"][5]
    assert slide["type"] == "cost_structure"
    assert "shareText" in slide["items"][0]  # camelCase, as src/types.ts expects
    assert "subtitle" in data["slides"][0]
    assert all("callout" not in s for s in data["slides"] if s["type"] == "key_takeaways")
