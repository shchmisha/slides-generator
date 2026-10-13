"""User materials as prompt text: tables with their ids and labels, documents as quoted data.

Everything here comes from uploaded files and is untrusted. It is wrapped in tags the system
prompt declares as data; user text cannot contain angle brackets at all (see `clean`), so a
document can neither close its block nor open one of its own.
"""

import re
import unicodedata

from app.facts.format import format_number
from app.facts.models import Table
from app.ingest.models import Document, Note

# Zero-width, bidi-control and BOM characters: invisible, and used to disguise tags and words.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
MAX_DOCUMENT_CHARS = 60_000


def clean(text: str) -> str:
    """User text as prompt data: normalised (NFKC folds full-width and look-alike forms),
    invisible characters removed, and angle brackets replaced, so no user string can form
    a tag — in any alphabet or spelling — and pose as our own markup."""
    text = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    return text.replace("<", "‹").replace(">", "›")


def render_table(table: Table) -> str:
    unit = f"; единица: {table.unit}" if table.unit else ""
    lines = [f"[{table.id}] {clean(table.title)} (источник: {clean(table.source)}{unit})"]
    header = [table.row_header, *(c.label for c in table.columns)]
    lines.append("| " + " | ".join(clean(h) for h in header) + " |")
    for row in table.rows:
        cells = []
        for column, cell in zip(table.columns, row.cells, strict=True):
            if not column.numeric:
                cells.append(clean(cell.text or ""))
            elif cell.value is None:
                cells.append("нет данных")
            else:
                cells.append(format_number(cell.value, cell.decimals))
        lines.append("| " + " | ".join([clean(row.label), *cells]) + " |")
    totals = [r.label for r in table.rows if r.is_total]
    if totals:
        lines.append(f"Итоговые строки: {', '.join(totals)}")
    units = [f"{c.label} — {c.unit}" for c in table.columns if c.unit]
    if units:
        lines.append(f"Единицы столбцов: {'; '.join(units)}")
    return "\n".join(lines)


def render_document(document: Document) -> str:
    lines = []
    for block in document.blocks:
        text = clean(block.text)
        if block.kind == "heading":
            lines.append(f"## {text}")
        elif block.kind == "list_item":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    body = "\n".join(lines)
    if len(body) > MAX_DOCUMENT_CHARS:
        body = body[:MAX_DOCUMENT_CHARS] + "\n[документ обрезан]"
    name = clean(document.filename).replace('"', "'")
    return f'<документ имя="{name}">\n{body}\n</документ>'


def render_materials(
    documents: list[Document],
    tables: list[Table],
    notes: list[Note],
    digest: list[str] | None = None,
) -> str:
    parts = []
    if tables:
        parts.append("<таблицы>\n" + "\n\n".join(render_table(t) for t in tables) + "\n</таблицы>")
    if digest:
        # Computed by code from the cleaned tables; labels inside it come from user files.
        lines = "\n".join(f"- {clean(line)}" for line in digest)
        parts.append(
            "<сводка_данных>\nПосчитано программой. Числа здесь — для понимания; "
            "в тексте слайдов числа только через {имя}.\n" + lines + "\n</сводка_данных>"
        )
    if notes:
        shown = [
            f"- {clean(n.location or n.source)}: {clean(n.message)}"
            for n in notes
            if n.kind == "data" and (n.severity == "warning" or "Исправлено" in n.message)
        ]
        removed = [n for n in notes if n.kind == "quarantine"]
        if removed:
            # The removed text itself never goes to the model, only the fact of removal.
            sources = ", ".join(sorted({clean(n.source) for n in removed}))
            shown.append(
                f"- {sources}: убрано фрагментов с указаниями для модели — {len(removed)} "
                "(не используются; пользователь увидит их на слайде «Проверка данных»)"
            )
        if shown:
            parts.append("<проверка_данных>\n" + "\n".join(shown) + "\n</проверка_данных>")
    text_documents = [d for d in documents if d.blocks]
    parts += [render_document(d) for d in text_documents]
    return "<материалы>\n" + "\n\n".join(parts) + "\n</материалы>"
