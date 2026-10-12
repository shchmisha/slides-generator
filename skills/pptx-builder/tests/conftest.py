from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from pptx_builder.layout import Layout

SKILL_DIR = Path(__file__).resolve().parent.parent

THEME: dict[str, Any] = {
    "name": "test",
    "colors": {
        "primary": "#1F3A5F",
        "accent": "#E8A33D",
        "text": "#1D1D1F",
        "muted": "#5B6472",
        "positive": "#2F7D4F",
        "negative": "#B3352B",
        "surface": "#F3F5F8",
        "grid": "#D8DDE4",
        "background": "#FFFFFF",
    },
    "fonts": {"heading": "Georgia", "body": "Arial"},
}


def rgba(hex_: str, alpha: float = 1) -> dict[str, Any]:
    return {"hex": hex_, "alpha": alpha}


def text_el(text: str, *, x: float = 64, y: float = 40, bold: bool = False) -> dict[str, Any]:
    return {
        "kind": "text",
        "box": {"x": x, "y": y, "w": 600, "h": 40},
        "valign": "top",
        "paras": [
            {
                "align": "left",
                "lineHeightPx": 40,
                "spaceAfterPx": 0,
                "runs": [
                    {
                        "text": text,
                        "font": "Georgia",
                        "sizePx": 32,
                        "bold": bold,
                        "italic": False,
                        "color": rgba("1F3A5F"),
                    }
                ],
            }
        ],
    }


def cell(
    text: str, *, bottom_px: float = 1, fill: str = "FFFFFF", alpha: float = 0
) -> dict[str, Any]:
    return {
        "text": text,
        "font": "Arial",
        "sizePx": 14,
        "bold": False,
        "color": rgba("1D1D1F"),
        "fill": rgba(fill, alpha),
        "align": "right",
        "padPx": [9, 10, 9, 10],
        "borderTop": {"px": 0, "color": rgba("000000", 0)},
        "borderBottom": {"px": bottom_px, "color": rgba("D8DDE4")},
    }


def chart_el(series: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    spec = {"categories": ["Июль", "Август", "Сентябрь"], "series": series, "valueFormat": "#,##0"}
    spec.update(extra)
    scales: dict[str, Any] = {"primary": {"min": 0, "max": 1000, "step": 200}}
    if any(s.get("axis") == "secondary" for s in series):
        scales["secondary"] = {"min": 0, "max": 15, "step": 3}
    return {
        "kind": "chart",
        "box": {"x": 64, "y": 160, "w": 640, "h": 380},
        "spec": spec,
        "scales": scales,
    }


def make_layout(*slides: list[dict[str, Any]], theme: dict[str, Any] | None = None) -> Layout:
    return Layout.model_validate(
        {
            "version": 1,
            "title": "Тест",
            "slideSize": {"widthPx": 1280, "heightPx": 720},
            "theme": theme or THEME,
            "slides": [
                {"index": i, "type": "test", "elements": elements}
                for i, elements in enumerate(slides)
            ],
            "issues": [],
            "previews": [],
            "timings": {},
        }
    )


@pytest.fixture
def logo_data_url() -> str:
    data = (SKILL_DIR / "examples" / "logo.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode()
