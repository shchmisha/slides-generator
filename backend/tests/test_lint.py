"""Wording rules: findings are precise on the live failures and silent on good text."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.facts.build import build_facts
from app.facts.store import FactStore
from app.ingest.extract import extract_file
from app.scenario.deck import DeckScenario
from app.scenario.lint import lint_deck
from app.scenario.video import VideoScenario, lint_video

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def store() -> FactStore:
    document = extract_file((FIXTURES / "q3_metrics.xlsx").read_bytes(), "q3_metrics.xlsx")
    return build_facts([document])[0]


@pytest.fixture
def board() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "board_scenario.json").read_text())
    return data


def findings(store: FactStore, data: dict[str, Any], count: int = 10) -> list[str]:
    return lint_deck(DeckScenario.model_validate(data), store.tables, count, (8, 10))


def test_reference_board_has_no_findings(store: FactStore, board: dict[str, Any]) -> None:
    assert findings(store, board) == []


def test_first_live_deck_findings(store: FactStore) -> None:
    data = json.loads((FIXTURES / "live/deck_q3_first_run.json").read_text())
    data["derived"] = [d for d in data["derived"] if d["op"] != "scale"]
    for slide in data["slides"]:
        if slide["type"] == "table":
            columns = slide["table"]["columns"]
            slide["table"]["columns"] = [
                c for c in columns if c["value"] != "complaints_per_1000_orders"
            ]
    found = findings(store, data, count=7)
    assert any(f.startswith("slides: в материалах просят 8–10 слайдов, а их 7") for f in found)
    assert any(f.startswith("slides[1].callout") and "это уровень" in f for f in found)
    assert any(f.startswith("slides[0].takeaways[2]") and "один период" in f for f in found)
    assert any(f.startswith("slides[4].title") for f in found)
    assert any(f.startswith("slides[6].risks[1].text") for f in found)


def test_level_used_as_gap(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][1]["callout"] = "Урал отстал от плана на {completion[Урал]}."
    assert any("это уровень" in f for f in findings(store, board))
    board["slides"][1]["callout"] = (
        "Урал выполнил план на {completion[Урал]}; недобор {deviation[Урал]}."
    )
    assert findings(store, board) == []


def test_trend_on_single_period_data(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][6]["callout"] = "Жалобы растут: {complaints_per_1000[Сибирь]} на 1000 заказов."
    assert any("один период" in f for f in findings(store, board))
    board["slides"][6]["callout"] = (
        "Больше всего жалоб на 1000 заказов — в Сибири: {complaints_per_1000[Сибирь]}."
    )
    assert findings(store, board) == []


def test_trend_on_periodic_data_is_fine(store: FactStore, board: dict[str, Any]) -> None:
    board["slides"][3]["callout"] = (
        "Выручка росла каждый месяц: {fact_by_month[Сентябрь]} в сентябре."
    )
    assert findings(store, board) == []


def test_slide_count_finding_only_outside_the_range(
    store: FactStore, board: dict[str, Any]
) -> None:
    assert findings(store, board, count=8) == []
    assert any("а их 11" in f for f in findings(store, board, count=11))


def test_video_lint_flags_ads_and_exclamations() -> None:
    video = VideoScenario.model_validate_json((FIXTURES / "live/video_first_run.json").read_text())
    source = (FIXTURES / "superservice_text.txt").read_text()
    found = " ".join(lint_video(video, source))
    for word in (
        "Хотите",
        "Знакомьтесь",
        "уже с нами",
        "Почему выбирают",
        "комиссий",
        "Присоединяйтесь",
        "Экономьте",
    ):
        assert word in found


def test_video_lint_allows_words_the_source_uses() -> None:
    scenes = [
        {"duration_s": 13, "narration": "Отключение услуги бесплатно, без дополнительных расходов.",
         "visual": {"type": "statement", "heading": "Без дополнительных расходов"}},
    ] * 5  # fmt: skip
    video = VideoScenario.model_validate(
        {"kind": "video", "title": "Суперсервис", "scenes": scenes}
    )
    assert lint_video(video, (FIXTURES / "superservice_text.txt").read_text()) == []
