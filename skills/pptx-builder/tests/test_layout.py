"""layout.json is validated strictly: drift between the Node and Python sides fails loudly."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from .conftest import THEME, chart_el, make_layout, text_el


def test_valid_layout_parses() -> None:
    layout = make_layout([text_el("Привет")])
    assert layout.slides[0].elements[0].kind == "text"
    assert layout.theme.rgb("primary") == "1F3A5F"


def test_unknown_element_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_layout([{"kind": "video", "box": {"x": 0, "y": 0, "w": 1, "h": 1}}])


def test_unknown_field_is_rejected() -> None:
    element = text_el("x")
    element["onclick"] = "alert(1)"
    with pytest.raises(ValidationError):
        make_layout([element])


def test_theme_must_define_every_color_token() -> None:
    theme: dict[str, Any] = {**THEME, "colors": {"primary": "#1F3A5F"}}
    with pytest.raises(ValidationError, match="missing colors"):
        make_layout([text_el("x")], theme=theme)


def test_bars_cannot_use_the_secondary_axis() -> None:
    series = [
        {"name": "A", "kind": "line", "values": [1, 2, 3], "color": "primary"},
        {"name": "B", "kind": "bar", "axis": "secondary", "values": [1, 2, 3], "color": "accent"},
    ]
    with pytest.raises(ValidationError, match="secondary axis"):
        make_layout([chart_el(series)])


def test_series_length_must_match_categories() -> None:
    series = [{"name": "A", "kind": "bar", "values": [1, 2], "color": "primary"}]
    with pytest.raises(ValidationError, match="one value per category"):
        make_layout([chart_el(series)])


def test_ragged_tables_are_rejected() -> None:
    table = {
        "kind": "table",
        "box": {"x": 0, "y": 0, "w": 10, "h": 10},
        "colW": [5, 5],
        "rowH": [10],
        "cells": [[]],
    }
    with pytest.raises(ValidationError, match="one cell per column"):
        make_layout([table])
