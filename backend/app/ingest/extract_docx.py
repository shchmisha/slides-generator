"""Word: paragraphs (headings, list items) and tables in document order."""

import io
import logging
import re

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ingest.errors import IngestError
from app.ingest.models import Block, Document, FileKind, Note, RawCell, RawTable

logger = logging.getLogger(__name__)

BULLETS = "•●▪‣◦·-–—*"
_HEADING_STYLE = re.compile(r"^(heading|заголовок)\s*(\d)?", re.IGNORECASE)
MAX_TITLE_CHARS = 120
MAX_HEADING_CHARS = 80


def extract_docx(data: bytes, filename: str) -> Document:
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises many types for broken files
        logger.warning("docx open failed for %s: %r", filename, exc)
        raise IngestError("Не удалось прочитать документ Word: файл повреждён") from exc

    blocks: list[Block] = []
    tables: list[RawTable] = []
    hidden = 0
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            if any(run.font.hidden for run in item.runs):
                hidden += 1
            block = _paragraph_block(item)
            if block is not None:
                blocks.append(block)
        elif isinstance(item, Table):
            previous = blocks[-1].text if blocks else None
            title = previous if previous and len(previous) <= MAX_TITLE_CHARS else None
            tables.append(_table(item, f"{filename} › таблица {len(tables) + 1}", title))
    notes = []
    if hidden:
        notes.append(
            Note(
                severity="warning",
                source=filename,
                message=f"Скрытый текст в документе пропущен (абзацев: {hidden}): "
                "его не видно читателю, поэтому он не используется",
            )
        )
    return Document(
        filename=filename, kind=FileKind.DOCX, blocks=blocks, tables=tables, notes=notes
    )


def _visible_text(paragraph: Paragraph) -> str:
    """Paragraph text without hidden runs (w:vanish): what a reader of the file actually sees."""
    if not any(run.font.hidden for run in paragraph.runs):
        return paragraph.text
    return "".join(run.text for run in paragraph.runs if not run.font.hidden)


def _paragraph_block(paragraph: Paragraph) -> Block | None:
    text = _visible_text(paragraph).strip()
    if not text:
        return None
    style = paragraph.style.name if paragraph.style is not None else ""
    if style == "Title":
        return Block(kind="heading", text=text, level=1)
    if match := _HEADING_STYLE.match(style):
        return Block(kind="heading", text=text, level=int(match.group(2) or 1))
    numbered = paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
    if numbered or (text[0] in BULLETS and len(text) > 1 and text[1].isspace()):
        return Block(kind="list_item", text=text.lstrip(BULLETS).strip())
    if _looks_like_heading(paragraph, text):
        # Many documents format headings by hand (bold "Normal" text) instead of using styles.
        return Block(kind="heading", text=text, level=2)
    return Block(kind="paragraph", text=text)


def _looks_like_heading(paragraph: Paragraph, text: str) -> bool:
    runs = [run for run in paragraph.runs if run.text.strip()]
    return (
        bool(runs)
        and all(run.bold for run in runs)
        and len(text) <= MAX_HEADING_CHARS
        and not text.endswith((".", ",", ";", ":"))
    )


def _table(table: Table, source: str, title: str | None) -> RawTable:
    rows: list[list[RawCell]] = []
    for r, row in enumerate(table.rows, start=1):
        cells: list[RawCell] = []
        seen: set[int] = set()
        for c, cell in enumerate(row.cells, start=1):
            if id(cell._tc) in seen:  # a merged cell repeats across the columns it spans
                continue
            seen.add(id(cell._tc))
            cells.append(
                RawCell(value=cell.text.strip() or None, address=f"строка {r}, столбец {c}")
            )
        rows.append(cells)
    width = max((len(row) for row in rows), default=0)
    rows = [row + [RawCell(value=None)] * (width - len(row)) for row in rows]
    return RawTable(source=source, title=title, rows=rows)
