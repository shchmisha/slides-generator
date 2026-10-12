"""Getting one JSON object out of a model reply that may wrap it in prose or ``` fences."""

import json
from typing import Any

_FENCE = "```"


class JsonOutputError(ValueError):
    """The reply holds no JSON object; the message goes back to the model."""


def extract_json_object(text: str) -> dict[str, Any]:
    body = text.strip()
    if _FENCE in body:
        parts = body.split(_FENCE)
        # The fenced block is the odd part; drop a language tag such as "json".
        fenced = [p.split("\n", 1)[1] if "\n" in p else p for p in parts[1::2]]
        body = max(fenced, key=len, default=body)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise JsonOutputError("В ответе нет JSON-объекта. Верни только JSON сценария.")
    try:
        value = json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JsonOutputError(
            f"JSON не разбирается: {exc.msg} (строка {exc.lineno}, столбец {exc.colno}). "
            "Верни корректный JSON целиком."
        ) from exc
    if not isinstance(value, dict):
        raise JsonOutputError("Ожидался JSON-объект сценария, а не список или значение.")
    return value
