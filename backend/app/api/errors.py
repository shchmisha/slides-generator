"""Validation errors in plain words, without echoing what the client sent.

FastAPI's default 422 body repeats the submitted input, which can put a password or a
document's text into logs and screenshots.
"""

import unicodedata
from collections.abc import Sequence
from typing import Any

_MESSAGES = {
    "missing": "обязательное поле",
    "string_too_short": "слишком короткое значение",
    "string_too_long": "слишком длинное значение",
    "too_long": "слишком много элементов",
    "literal_error": "недопустимое значение",
    "enum": "недопустимое значение",
    "extra_forbidden": "неизвестное поле",
    "greater_than_equal": "вне допустимого диапазона",
    "less_than_equal": "вне допустимого диапазона",
    "greater_than": "вне допустимого диапазона",
    "less_than": "вне допустимого диапазона",
    "json_invalid": "некорректный JSON",
    "uuid_parsing": "некорректный идентификатор",
}
_LOCATIONS = {"body", "query", "path", "header", "cookie"}
MAX_DETAIL_CHARS = 500
MAX_FIELD_CHARS = 40


def _field_part(part: object) -> str:
    # Dict keys come from the client: keep them short and printable.
    text = "".join(ch for ch in str(part) if unicodedata.category(ch)[0] != "C")
    return text if len(text) <= MAX_FIELD_CHARS else text[: MAX_FIELD_CHARS - 1] + "…"


def describe_errors(errors: Sequence[Any]) -> str:
    parts = []
    for error in errors:
        field = ".".join(_field_part(p) for p in error.get("loc", ()) if p not in _LOCATIONS)
        if error.get("type") == "value_error":  # our own validators already speak Russian
            message = str(error.get("msg", "")).removeprefix("Value error, ")
        else:
            message = _MESSAGES.get(error.get("type", ""), "некорректное значение")
        parts.append(f"{field}: {message}" if field else message)
    text = "; ".join(dict.fromkeys(parts)) or "некорректный запрос"
    return text if len(text) <= MAX_DETAIL_CHARS else text[: MAX_DETAIL_CHARS - 1] + "…"
