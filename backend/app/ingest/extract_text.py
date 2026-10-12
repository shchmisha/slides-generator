"""Plain text (typed into the form or a .txt/.md file): paragraphs, bullets, # headings."""

import re

from app.ingest.extract_docx import BULLETS
from app.ingest.models import Block, Document, FileKind

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


def extract_text(text: str, name: str = "Текст") -> Document:
    blocks: list[Block] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(Block(kind="paragraph", text=" ".join(paragraph)))
            paragraph.clear()

    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            flush()
        elif heading := _HEADING.match(line):
            flush()
            blocks.append(Block(kind="heading", text=heading.group(2), level=len(heading.group(1))))
        elif line[0] in BULLETS and len(line) > 1 and line[1].isspace():
            flush()
            blocks.append(Block(kind="list_item", text=line.lstrip(BULLETS).strip()))
        else:
            paragraph.append(line)
    flush()
    return Document(filename=name, kind=FileKind.TEXT, blocks=blocks, tables=[], notes=[])
