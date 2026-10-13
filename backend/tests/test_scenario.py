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
    assert len(result.spec.slides) == 10  # 9 + «Проверка данных» appended for the 12 % claim
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


# ------------------------------------------------------------------ number integrity (3c)


def test_rate_gives_complaints_per_thousand_orders(store: FactStore, board: dict[str, Any]) -> None:
    board["derived"] = [
        d for d in board["derived"] if d["id"] not in ("complaint_rate", "complaints_per_1000")
    ]
    board["derived"].append({
        "id": "complaints_per_1000", "op": "rate", "label": "Жалоб на 1000 заказов",
        "a": {"name": "complaints"}, "b": {"name": "orders"}, "per": 1000,
    })  # fmt: skip
    table = resolve(store, board).spec.slides[6].table  # type: ignore[union-attr]
    rate = {row[0].text: row[5] for row in table.rows}
    assert {k: c.text for k, c in rate.items()} == {
        "Север": "7,5", "Юг": "9,2", "Центр": "6,9", "Урал": "11,0", "Сибирь": "13,9",
    }  # fmt: skip
    assert rate["Сибирь"].bold  # "highest" marks the worst branch, not the biggest one


def test_scale_cannot_manufacture_a_figure(board: dict[str, Any]) -> None:
    board["derived"].append({
        "id": "revenue_mgmt", "op": "scale", "label": "Управленческая выручка",
        "a": {"name": "rev_fact"}, "factor": 3.4755, "unit": "млн ₽",
    })  # fmt: skip
    with pytest.raises(ValidationError, match="перевод единиц"):
        DeckScenario.model_validate(board)


def test_rate_phrase_and_hundred_percent_are_allowed(
    store: FactStore, board: dict[str, Any]
) -> None:
    board["slides"][6]["callout"] = "Жалобы считаем на 1000 заказов; 100 % — полное выполнение."
    resolve(store, board)
    board["slides"][6]["callout"] = "Жалоб больше 5000, а выручка — девять тысяч."
    errors = errors_of(store, board)
    assert any("«5000»" in e and "«тысяч»" in e for e in errors)


def test_typed_numbers_in_headers_are_rejected(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][1]["table"]["columns"][0]["header"] = "План 2 835"
    assert any("columns[0].header" in e for e in errors_of(store, board))


def test_claim_lineage_through_scale_is_rejected(store: FactStore, board: dict[str, Any]) -> None:
    board["derived"].append({
        "id": "rev_thousands", "op": "scale", "label": "Выручка, тыс.", "a": {"name": "rev_fact"},
        "factor": 1000, "unit": "тыс. ₽",
    })  # fmt: skip
    board["claims"] = [{"source": "x.docx", "quote": "выручка 2 877 000", "value": "rev_thousands"}]
    assert any("только данными таблиц" in e for e in errors_of(store, board))


def test_claims_take_series_items_and_digitless_quotes_are_not_mismatches(
    store: FactStore, board: dict[str, Any]
) -> None:
    marketing = "в сентябре выросли расходы на маркетинг"
    board["claims"] = [
        {"source": "c.docx", "quote": "Урал выполнил план на 96,6 %", "value": "completion[Урал]"},
        {"source": "c.docx", "quote": marketing, "value": "cost_change[Маркетинг]"},
    ]
    result = resolve(store, board)
    assert [c.matches for c in result.claims] == [True, None]
    assert len(result.spec.slides) == 9  # nothing to report → no «Проверка данных» slide


# ------------------------------------------------------------------ tolerance (3c)


def test_original_keys_find_positionally_aligned_items(
    store: FactStore, board: dict[str, Any]
) -> None:
    board["slides"][4]["callout"] = "Маржа в сентябре — {margin_by_month[Сентябрь факт]}."
    slide = resolve(store, board).spec.slides[4]
    assert slide.callout == "Маржа в сентябре — 13,3 %."  # type: ignore[union-attr]


def test_inline_references_and_stray_claim_keys_are_accepted(
    store: FactStore, board: dict[str, Any]
) -> None:
    board["slides"][5]["amounts"] = {"table": "t2", "col": "Итого"}
    board["slides"][5]["change"] = {"name": "cost_change"}
    board["claims"][0]["note"] = "проверено"
    scenario = DeckScenario.model_validate(board)
    assert scenario.slides[5].amounts.startswith("auto_")  # type: ignore[union-attr]
    resolve_deck(scenario, store)


# ------------------------------------------------------------------ autofixes (3c)


def test_small_line_moves_to_the_secondary_axis(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][3]["chart"]["series"] = [
        {"name": "Выручка", "value": "fact_by_month"},
        {"name": "Прибыль", "value": "profit_by_month", "kind": "line"},
    ]
    result = resolve(store, board)
    profit = result.spec.slides[3].chart.series[1]  # type: ignore[union-attr]
    assert profit.axis == "secondary"
    assert any("вспомогательную ось" in w for w in result.warnings)


def test_data_notes_slide_lists_fixes_gaps_and_claims(
    store: FactStore, board: dict[str, Any]
) -> None:
    document = extract_file((FIXTURES / "q3_metrics.xlsx").read_bytes(), "q3_metrics.xlsx")
    _, notes = build_facts([document])
    result = resolve_deck(DeckScenario.model_validate(board), store, notes)
    slide = result.spec.slides[-1]
    assert isinstance(slide, rd.TableSlide)
    text = [" | ".join(c.text for c in row) for row in slide.table.rows]
    assert any("Юг › Июль факт (C5)" in t and "«224,0»" in t for t in text)
    assert any("Сибирь › NPS (D8)" in t for t in text)
    assert any("12%" in t and "8,2" in t for t in text)
    assert not any("Проверено" in t for t in text)


# ------------------------------------------------------------------ live regressions (3c)

LIVE = FIXTURES / "live"


def test_live_reply_that_failed_on_original_keys_now_resolves(store: FactStore) -> None:
    from app.llm.jsonout import extract_json_object

    data = extract_json_object((LIVE / "deck_failed_key_mismatch.txt").read_text())
    errors = errors_of(store, data)
    # The «Сентябрь факт» key now resolves; only the new rate rule is left to fix.
    assert errors and all("rate" in e for e in errors)


def test_live_scale_attack_is_rejected() -> None:
    data = json.loads((LIVE / "deck_scale_attack.json").read_text())
    with pytest.raises(ValidationError, match="перевод единиц"):
        DeckScenario.model_validate(data)


def test_first_live_deck_complaint_column_is_now_an_error(store: FactStore) -> None:
    data = json.loads((LIVE / "deck_q3_first_run.json").read_text())
    errors = errors_of(store, data)  # scale ×1000 of a raw count labelled «на 1000 заказов»
    assert any("complaints_per_1000_orders" in e and "rate" in e for e in errors)


def test_rate_base_may_label_a_column(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][6]["table"]["columns"][4]["header"] = "Жалоб / 1000"  # rate per=1000 exists
    resolve(store, board)
    board["derived"] = [d for d in board["derived"] if d["op"] != "rate"]
    board["slides"][6]["table"]["columns"] = board["slides"][6]["table"]["columns"][:4]
    board["slides"][7]["chart"]["series"] = board["slides"][7]["chart"]["series"][1:]
    board["slides"][7]["callout"] = None
    board["slides"][8]["risks"][1]["text"] = "Доставка в срок {on_time[Сибирь]}."
    board["slides"][3]["callout"] = "Жалоб / 1000 — без операции rate нельзя."
    assert any("«1000»" in e for e in errors_of(store, board))
