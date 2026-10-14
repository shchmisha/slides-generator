"""User edits go through the same checks as the model's reply, against the stored basis."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from app.agent.planner import plan_deck
from app.agent.review import Basis, DeckBasis, VideoBasis, deck_basis, review
from app.agent.settings import DeckSettings, VideoSettings
from app.facts.build import build_facts
from app.ingest.extract import extract_file

from .fakes import ScriptedModel

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = (
    "Суперсервис — это 12 услуг в одном окне. Заказ оформляется за 5 минут, "
    "доставка работает в 1 200 городах."
)


@pytest.fixture(scope="module")
def board() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "board_scenario.json").read_text())
    return data


@pytest.fixture(scope="module")
def basis(board: dict[str, Any]) -> DeckBasis:
    documents = [
        extract_file((FIXTURES / name).read_bytes(), name)
        for name in ("q3_metrics.xlsx", "manager_comments.docx")
    ]
    store, notes = build_facts(documents)
    plan = plan_deck(documents, store, notes, DeckSettings(), ScriptedModel(json.dumps(board)))
    return deck_basis(plan, DeckSettings())


def test_basis_survives_a_round_trip_through_json(basis: DeckBasis) -> None:
    stored = json.loads(basis.model_dump_json())
    restored = TypeAdapter[Any](Basis).validate_python(stored)
    assert isinstance(restored, DeckBasis) and restored == basis


def test_unchanged_scenario_passes_with_the_slides_to_review(
    board: dict[str, Any], basis: DeckBasis
) -> None:
    result = review(board, basis)
    assert result.ok, result.errors
    preview = result.report["preview"]
    assert len(preview["slides"]) == result.report["slides"] >= 9
    # Numbers are filled in, typeset with a no-break space.
    assert "2\u00a0877" in json.dumps(preview, ensure_ascii=False)
    assert result.report["claims"]  # the manager's "~12 %" is checked against the table


def test_wording_edit_is_accepted(board: dict[str, Any], basis: DeckBasis) -> None:
    edited = copy.deepcopy(board)
    edited["slides"][0]["title"] = "Итоги квартала"
    result = review(edited, basis)
    assert result.ok and result.content is not None
    assert result.content["slides"][0]["title"] == "Итоги квартала"


def test_a_typed_number_is_rejected_like_from_the_model(
    board: dict[str, Any], basis: DeckBasis
) -> None:
    edited = copy.deepcopy(board)
    edited["slides"][0]["takeaways"][0] = "Выручка выросла на 47 % за квартал."
    result = review(edited, basis)
    assert not result.ok
    assert any("47" in error for error in result.errors)


def test_unknown_reference_and_broken_schema_are_rejected(
    board: dict[str, Any], basis: DeckBasis
) -> None:
    edited = copy.deepcopy(board)
    edited["slides"][0]["takeaways"][0] = "Рост составил {no_such_value}."
    assert any("no_such_value" in e for e in review(edited, basis).errors)
    assert review({"title": "x"}, basis).content is None


def test_video_edit_is_fitted_and_checked() -> None:
    basis = VideoBasis(source_text=SOURCE, settings=VideoSettings())

    def scenario(*narrations: str, seconds: float) -> dict[str, Any]:
        return {
            "kind": "video",
            "title": "Что такое Суперсервис",
            "scenes": [
                {"duration_s": seconds, "narration": n, "visual": {"type": "statement"}}
                for n in narrations
            ],
        }

    ok = review(
        scenario(
            "Суперсервис — это 12 услуг в одном окне.",
            "Заказ оформляется за 5 минут.",
            "Доставка работает в 1 200 городах.",
            "Всё в одном месте.",
            "Без лишних звонков.",
            "Попробуйте сами.",
            seconds=13.5,  # 81 s in total: scaled down into 60–75 s
        ),
        basis,
    )
    assert ok.ok, ok.errors
    assert ok.content is not None
    assert 60 <= sum(s["duration_s"] for s in ok.content["scenes"]) <= 75
    assert ok.report["warnings"]  # the fitting is reported

    bad = review(scenario(*["Сервис экономит 30 % времени."] * 6, seconds=11), basis)
    assert any("30" in e for e in bad.errors)


# ------------------------------------------------------------------ review findings, regressions


def test_values_resolve_whatever_order_their_keys_come_in(
    board: dict[str, Any], basis: DeckBasis
) -> None:
    edited = copy.deepcopy(board)
    edited["values"] = {"q": {"name": "rev_fact"}, **dict(reversed(board["values"].items()))}
    edited["slides"][0]["title"] = "Выручка {q}"
    result = review(edited, basis)
    assert result.ok, result.errors


def test_claim_must_quote_the_materials(board: dict[str, Any], basis: DeckBasis) -> None:
    claim = copy.deepcopy(board["claims"][0])
    invented = copy.deepcopy(board)
    invented["claims"] = [{**claim, "quote": "выручка составила 555 млн ₽"}]
    assert any("такой фразы нет" in e for e in review(invented, basis).errors)

    wrong_file = copy.deepcopy(board)
    wrong_file["claims"] = [{**claim, "source": "Итог 2025: 777 млн ₽"}]
    assert any("укажи файл" in e for e in review(wrong_file, basis).errors)

    restyled = copy.deepcopy(board)  # case, spacing and quote style do not matter
    restyled["claims"] = [{**claim, "quote": "  " + claim["quote"].upper().replace("%", " %")}]
    assert review(restyled, basis).ok


def test_invisible_characters_cannot_hide_a_typed_number(
    board: dict[str, Any], basis: DeckBasis
) -> None:
    edited = copy.deepcopy(board)
    edited["slides"][0]["takeaways"][0] = "Выручка 9\u200b9\u200b9 млн ₽ за квартал."
    result = review(edited, basis)
    assert any("999" in e for e in result.errors)


def test_on_n_phrase_needs_a_rate_per_n(board: dict[str, Any], basis: DeckBasis) -> None:
    edited = copy.deepcopy(board)
    edited["slides"][0]["takeaways"][0] = "Клиентов стало больше на 10000 человек."
    assert any("10000" in e for e in review(edited, basis).errors)


def test_absurd_magnitude_is_an_error_not_a_crash(board: dict[str, Any], basis: DeckBasis) -> None:
    edited = copy.deepcopy(board)
    chain = [
        {"id": f"big{i}", "op": "scale", "label": "x", "factor": 1000000,
         "a": {"name": "rev_fact" if i == 0 else f"big{i - 1}"}}
        for i in range(5)
    ]  # fmt: skip
    edited["derived"] = [*board["derived"], *chain]
    edited["slides"][0]["title"] = "Итог {big4}"
    result = review(edited, basis)
    assert any("10^15" in e for e in result.errors)


def test_nul_and_lone_surrogates_in_scenario_text(board: dict[str, Any], basis: DeckBasis) -> None:
    edited = copy.deepcopy(board)
    edited["slides"][0]["title"] = "Итоги\x00 квартала"
    result = review(edited, basis)
    assert result.ok and result.content is not None
    assert result.content["slides"][0]["title"] == "Итоги квартала"
    edited["slides"][0]["title"] = "Итоги \ud800"
    assert review(edited, basis).content is None


def test_video_edit_keeps_the_notes_shown_to_the_user() -> None:
    from app.ingest.models import Note

    note = Note(severity="warning", source="текст", message="Удалена инструкция", kind="quarantine")
    basis = VideoBasis(source_text=SOURCE, settings=VideoSettings(), notes=[note])
    content = {
        "kind": "video",
        "title": "Что такое Суперсервис",
        "scenes": [
            {"duration_s": 12, "narration": n, "visual": {"type": "statement"}}
            for n in ("Суперсервис — это 12 услуг в одном окне.", *["Всё в одном месте."] * 5)
        ],
    }
    assert review(content, basis).report["notes"][0]["message"] == "Удалена инструкция"


def test_report_maps_preview_slides_and_lists_values(
    board: dict[str, Any], basis: DeckBasis
) -> None:
    report = review(board, basis).report
    count = len(board["slides"])
    # The «Проверка данных» slide was not in the scenario: added automatically, at the end.
    assert report["preview_origins"] == [*range(count), None]
    names = {v["name"]: v for v in report["values"]}
    assert names["rev_fact"]["value"] == "2\u00a0877\u00a0млн ₽"
    assert any(i["key"] == "Урал" for i in names["completion"]["items"])

    with_notes = copy.deepcopy(board)
    with_notes["slides"].insert(1, {"type": "data_notes"})
    origins = review(with_notes, basis).report["preview_origins"]
    assert origins == list(range(count + 1))  # placed where the scenario put it
