"""Video scenario checks: duration, speakable narration, facts only from the source text."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.scenario.video import VideoScenario, check_video

SOURCE = (
    "Суперсервис — это 12 услуг в одном окне. Заказ оформляется за 5 минут, "
    "доставка работает в 1 200 городах."
)


def scene(
    seconds: float, narration: str = "Короткая фраза о сервисе.", **visual: Any
) -> dict[str, Any]:
    return {
        "duration_s": seconds,
        "narration": narration,
        "visual": {"type": "statement", **visual},
    }


def video(*scenes: dict[str, Any]) -> VideoScenario:
    return VideoScenario.model_validate(
        {"kind": "video", "title": "Что такое Суперсервис", "scenes": list(scenes)}
    )


def test_valid_video_has_no_problems() -> None:
    scenario = video(
        scene(12, "Суперсервис — это 12 услуг в одном окне."),
        scene(14, "Заказ оформляется за 5 минут.", type="big_number", number="5 минут"),
        scene(14, "Доставка работает в 1 200 городах."),
        scene(12, items=["Одно окно", "Быстрый заказ"], type="bullets"),
        scene(10),
    )
    assert check_video(scenario, SOURCE) == []


def test_total_duration_must_be_60_to_75_seconds() -> None:
    problems = check_video(video(scene(10), scene(10), scene(10), scene(10)), SOURCE)
    assert any("Общая длительность 40" in p for p in problems)


def test_numbers_not_in_the_source_are_rejected() -> None:
    scenario = video(scene(15, "Сервис экономит 30 % времени."), scene(15), scene(15), scene(15))
    problems = check_video(scenario, SOURCE)
    assert any("числа 30 нет в исходном тексте" in p for p in problems)


def test_narration_must_fit_its_scene() -> None:
    long_text = " ".join(["слово"] * 40)
    scenario = video(scene(5, long_text), scene(20 - 5), scene(15), scene(15), scene(15))
    assert any("не успеть сказать" in p for p in check_video(scenario, SOURCE))


def test_visual_requirements() -> None:
    scenario = video(
        scene(15, type="big_number"),
        scene(15, type="bullets", items=["один"]),
        scene(15),
        scene(15),
    )
    problems = check_video(scenario, SOURCE)
    assert any("нужно поле number" in p for p in problems)
    assert any("нужно 2–5 пунктов" in p for p in problems)


def test_schema_rejects_html_and_unknown_visuals() -> None:
    with pytest.raises(ValidationError):
        video(scene(15, type="html"), scene(15), scene(15), scene(15))
    with pytest.raises(ValidationError):
        VideoScenario.model_validate(
            {"kind": "video", "title": "x", "scenes": [scene(15)] * 4, "html": "<script></script>"}
        )
