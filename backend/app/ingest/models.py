"""What the extractors produce: text blocks and raw tables, plus notes for the user."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

RawValue = str | int | float | None


class FileKind(StrEnum):
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    TEXT = "text"


class Note(BaseModel):
    """A fix, gap or check result reported to the user in the data-quality summary."""

    model_config = ConfigDict(frozen=True)

    severity: Literal["info", "warning"]
    source: str  # file name, or "file › sheet"
    location: str | None = None  # cell address or row/column labels
    message: str
    # "quarantine": text removed as an instruction to the model. Its message quotes that text
    # for the user, so it is never shown to the model.
    kind: Literal["data", "quarantine"] = "data"


class Block(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["heading", "paragraph", "list_item"]
    text: str
    level: int = 0


class RawCell(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: RawValue
    address: str | None = None  # "C4" for spreadsheet cells


class RawTable(BaseModel):
    """A rectangular block of cells as found in the file, before any cleaning."""

    model_config = ConfigDict(frozen=True)

    source: str  # "q3_metrics.xlsx › Выручка"
    title: str | None
    rows: list[list[RawCell]]


class Document(BaseModel):
    filename: str
    kind: FileKind
    blocks: list[Block]
    tables: list[RawTable]
    notes: list[Note]
