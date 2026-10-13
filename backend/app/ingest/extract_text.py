"""Plain text (typed into the form or a .txt/.md file): one block per line.

Text copied from a web page often has no blank lines between paragraphs, so every non-empty
line is its own block (a heading, a bullet, a numbered step or a paragraph). This also keeps
the quarantine precise: one suspicious line is removed, not the paragraph around it.
"""

import re

from app.ingest.extract_docx import BULLETS
from app.ingest.models import Block, Document, FileKind

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_NUMBERED = re.compile(r"^\d{1,2}[.)]\s+\S")  # "1. Подключение оператора ЭДО"


def extract_text(text: str, name: str = "Текст") -> Document:
    blocks: list[Block] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if heading := _HEADING.match(line):
            blocks.append(Block(kind="heading", text=heading.group(2), level=len(heading.group(1))))
        elif line[0] in BULLETS and len(line) > 1 and line[1].isspace():
            blocks.append(Block(kind="list_item", text=line.lstrip(BULLETS).strip()))
        elif _NUMBERED.match(line):
            blocks.append(Block(kind="list_item", text=line, level=1))  # keeps "1." for steps
        else:
            blocks.append(Block(kind="paragraph", text=line))
    return Document(filename=name, kind=FileKind.TEXT, blocks=blocks, tables=[], notes=[])
