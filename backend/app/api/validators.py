"""Checks shared by request models.

Postgres text cannot hold NUL, and a lone UTF-16 surrogate (valid in JSON as "\ud800") cannot
be encoded to UTF-8 at all; either would surface as a 500 deep inside the driver or the
password hasher. They are refused at the edge with a normal 422 instead.
"""

import unicodedata


def no_control_chars(value: str) -> str:
    """For one-line fields (titles, emails): no control characters, no lone surrogates."""
    if any(unicodedata.category(ch) in ("Cc", "Cs") for ch in value):
        raise ValueError("недопустимые символы")
    return value


def encodable(value: str) -> str:
    """For free text and passwords: tabs and line breaks are fine, NUL and surrogates are not."""
    if "\x00" in value or any(unicodedata.category(ch) == "Cs" for ch in value):
        raise ValueError("недопустимые символы")
    return value
