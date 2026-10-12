"""Entry point: bytes of an uploaded file (or typed text) → Document."""

from app.ingest.errors import IngestError
from app.ingest.extract_docx import extract_docx
from app.ingest.extract_pptx import extract_pptx
from app.ingest.extract_text import extract_text
from app.ingest.extract_xlsx import extract_xlsx
from app.ingest.models import Document, FileKind
from app.ingest.sniff import sniff

__all__ = ["IngestError", "extract_file", "extract_text"]


def extract_file(data: bytes, filename: str) -> Document:
    sniffed = sniff(data, filename)
    match sniffed.kind:
        case FileKind.DOCX:
            document = extract_docx(sniffed.data, filename)
        case FileKind.XLSX:
            document = extract_xlsx(sniffed.data, filename)
        case FileKind.PPTX:
            document = extract_pptx(sniffed.data, filename)
        case FileKind.TEXT:
            document = extract_text(sniffed.data.decode("utf-8"), filename)
    document.notes[:0] = sniffed.notes
    return document
