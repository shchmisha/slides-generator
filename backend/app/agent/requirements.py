"""Requirements stated in the materials themselves, e.g. "От 8 до 10 слайдов"."""

import re

from app.ingest.models import Document

_RANGE = re.compile(r"(?:от\s*)?(\d{1,2})\s*(?:до|–|—|-)\s*(\d{1,2})\s*слайд", re.IGNORECASE)
_EXACT = re.compile(r"(?<![\d–—-])(\d{1,2})\s*слайд", re.IGNORECASE)
MAX_SLIDES = 15


def slide_range(documents: list[Document]) -> tuple[int, int] | None:
    """The slide count the materials ask for, as (min, max); None when they do not say."""
    for document in documents:
        for block in document.blocks:
            if match := _RANGE.search(block.text):
                low, high = int(match.group(1)), int(match.group(2))
                if 1 <= low <= high <= MAX_SLIDES:
                    return low, high
    for document in documents:
        for block in document.blocks:
            if match := _EXACT.search(block.text):
                count = int(match.group(1))
                if 1 <= count <= MAX_SLIDES:
                    return count, count
    return None
