"""The data summary states directions, rankings and gaps so the model does not compute them."""

from pathlib import Path

import pytest

from app.agent.requirements import slide_range
from app.facts.build import build_facts
from app.facts.digest import build_digest, is_single_period
from app.ingest.extract import extract_file, extract_text

FIXTURES = Path(__file__).parent / "fixtures"
NBSP = " "


@pytest.fixture(scope="module")
def digest() -> str:
    document = extract_file((FIXTURES / "q3_metrics.xlsx").read_bytes(), "q3_metrics.xlsx")
    store, _ = build_facts([document])
    return "\n".join(build_digest(store.tables))


def test_profit_and_margin_directions(digest: str) -> None:
    assert "июль 124 → август 105 → сентябрь 133 (↓ ↑)" in digest
    assert "маржа: 13,2 % → 11,1 % → 13,3 % (↓ ↑)" in digest
    assert "выручка +6,5 %, затраты +6,4 %" in digest


def test_single_period_table_is_marked(digest: str) -> None:
    assert "[t3] «Операционные показатели, 3 квартал» — один период" in digest


def test_rankings_name_missing_values_and_lower_is_better(digest: str) -> None:
    assert "«NPS» по убыванию: Центр 58; Север 52; Юг 47; Урал 44; нет данных: Сибирь" in digest
    assert "«Жалоб, шт.» по убыванию (меньше — лучше)" in digest
    assert "на 1000 «Заказов, шт.» по убыванию: Сибирь 13,9; Урал 11,0" in digest


def test_below_plan_and_prior_year(digest: str) -> None:
    assert "Урал — июль (−8), сентябрь (−11)" in digest
    assert "ни в одном периоде не ниже плана: Юг" in digest
    assert "Урал +2,0 %. Ниже прошлого года — никто" in digest


def test_prior_year_costs_are_never_paired_with_revenue(digest: str) -> None:
    assert "[t4 − t2]" not in digest  # last year's revenue minus this year's costs is meaningless


def test_is_single_period() -> None:
    document = extract_file((FIXTURES / "q3_metrics.xlsx").read_bytes(), "q3_metrics.xlsx")
    tables = build_facts([document])[0].tables
    assert [is_single_period(t) for t in tables] == [False, False, True, False]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("От 8 до 10 слайдов, деловой тон", (8, 10)),
        ("Нужно 5–7 слайдов", (5, 7)),
        ("Сделайте 6 слайдов", (6, 6)),
        ("Без указаний на объём", None),
    ],
)
def test_slide_range_from_materials(text: str, expected: tuple[int, int] | None) -> None:
    assert slide_range([extract_text(text)]) == expected


def test_slide_range_from_client_comments() -> None:
    comments = extract_file(
        (FIXTURES / "manager_comments.docx").read_bytes(), "manager_comments.docx"
    )
    assert slide_range([comments]) == (8, 10)
