"""Documents → fact store: number the tables across all files and clean them."""

from app.facts.store import FactStore
from app.facts.tables import normalize
from app.ingest.models import Document, Note


def build_facts(documents: list[Document]) -> tuple[FactStore, list[Note]]:
    tables = []
    notes: list[Note] = [note for doc in documents for note in doc.notes]
    raw_tables = [raw for doc in documents for raw in doc.tables if len(raw.rows) >= 2]
    for number, raw in enumerate(raw_tables, start=1):
        table, table_notes = normalize(raw, f"t{number}")
        tables.append(table)
        notes += table_notes
    return FactStore(tables), notes
