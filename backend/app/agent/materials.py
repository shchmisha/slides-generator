"""User materials as prompt text: tables with their ids and labels, documents as quoted data.

Everything here comes from uploaded files and is untrusted. It is wrapped in tags the system
prompt declares as data, and anything that looks like our own tags is neutralised so a document
cannot close the block and pose as instructions.
"""

import re

from app.facts.format import format_number
from app.facts.models import Table
from app.ingest.models import Document, Note

_OUR_TAGS = re.compile(r"</?\s*(документ|таблицы|проверка_данных|материалы)[^>]*>", re.IGNORECASE)
MAX_DOCUMENT_CHARS = 60_000


def _clean(text: str) -> str:
    return _OUR_TAGS.sub("[тег удалён]", text)


def render_table(table: Table) -> str:
    unit = f"; единица: {table.unit}" if table.unit else ""
    lines = [f"[{table.id}] {_clean(table.title)} (источник: {_clean(table.source)}{unit})"]
    header = [table.row_header, *(c.label for c in table.columns)]
    lines.append("| " + " | ".join(_clean(h) for h in header) + " |")
    for row in table.rows:
        cells = []
        for column, cell in zip(table.columns, row.cells, strict=True):
            if not column.numeric:
                cells.append(_clean(cell.text or ""))
            elif cell.value is None:
                cells.append("нет данных")
            else:
                cells.append(format_number(cell.value, cell.decimals))
        lines.append("| " + " | ".join([_clean(row.label), *cells]) + " |")
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
        text = _clean(block.text)
        if block.kind == "heading":
            lines.append(f"## {text}")
        elif block.kind == "list_item":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    body = "\n".join(lines)
    if len(body) > MAX_DOCUMENT_CHARS:
        body = body[:MAX_DOCUMENT_CHARS] + "\n[документ обрезан]"
    return f'<документ имя="{_clean(document.filename)}">\n{body}\n</документ>'


def render_materials(documents: list[Document], tables: list[Table], notes: list[Note]) -> str:
    parts = []
    if tables:
        parts.append("<таблицы>\n" + "\n\n".join(render_table(t) for t in tables) + "\n</таблицы>")
    if notes:
        shown = [
            f"- {_clean(n.location or n.source)}: {_clean(n.message)}"
            for n in notes
            if n.severity == "warning" or "Исправлено" in n.message
        ]
        if shown:
            parts.append("<проверка_данных>\n" + "\n".join(shown) + "\n</проверка_данных>")
    text_documents = [d for d in documents if d.blocks]
    parts += [render_document(d) for d in text_documents]
    return "<материалы>\n" + "\n\n".join(parts) + "\n</материалы>"
