"""A claim quotes the materials, and the quote must really be in the file it names.

The «Проверка данных» slide prints the quote next to the figure from the table, so a quote that
is not in the materials would put an invented figure on a slide, whether the model or the user
wrote it. The comparison ignores case, spacing, the style of quotes and dashes, and «ё».
"""

import re
from collections.abc import Sequence

from app.scenario.deck import Claim

_UNIFY = str.maketrans(
    {
        "«": '"',
        "»": '"',
        "“": '"',
        "”": '"',
        "„": '"',
        "'": '"',
        "–": "-",
        "—": "-",
        " ": " ",
        " ": " ",
        "ё": "е",
    }
)


def normalise(text: str) -> str:
    text = re.sub(r"\s+", " ", text.casefold().translate(_UNIFY))
    return re.sub(r"\s+%", "%", text).strip(' .,;:!?"')


def quote_errors(claims: Sequence[Claim], sources: dict[str, str]) -> list[str]:
    """`sources`: file name → its text after quarantine."""
    texts = {normalise(name): normalise(text) for name, text in sources.items()}
    names = ", ".join(sources) or "нет файлов с текстом"
    errors = []
    for i, claim in enumerate(claims):
        source = normalise(claim.source)
        matching = [text for name, text in texts.items() if name and name in source]
        if not matching:
            errors.append(f"claims[{i}].source: укажи файл, откуда цитата: {names}")
        elif not any(normalise(claim.quote) in text for text in matching):
            errors.append(
                f"claims[{i}].quote: такой фразы нет в «{claim.source}» — приведи её дословно"
            )
    return errors
