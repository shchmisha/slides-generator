"""Scenario strings, from the model or the user: invisible and control characters out.

A zero-width space would let "9​9​9" pass the typed-number check as three small
numbers while the slide shows 999; NUL cannot be stored in Postgres JSONB; a lone surrogate
cannot be encoded at all. Format characters (zero-width, bidi marks, soft hyphen) are dropped,
other control characters except line breaks and tabs are dropped, lone surrogates are refused.
"""

import unicodedata


def clean_text(value: str) -> str:
    kept = []
    for ch in value:
        category = unicodedata.category(ch)
        if category == "Cs":
            raise ValueError("недопустимый символ")
        if category == "Cf" or (category == "Cc" and ch not in "\n\t"):
            continue
        kept.append(ch)
    cleaned = "".join(kept)
    if value and not cleaned.strip():
        raise ValueError("пустой текст")
    return cleaned
