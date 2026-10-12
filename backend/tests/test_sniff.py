"""Uploads are identified by content; hostile containers are rejected before any parser runs."""

import io
import zipfile

import docx
import pytest

from app.ingest.errors import IngestError
from app.ingest.models import FileKind
from app.ingest.sniff import MAX_ENTRIES, sniff

DOCM_CT = "application/vnd.ms-word.document.macroEnabled.main+xml"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
VBA_REL = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"


def make_docx(text: str = "Привет") -> bytes:
    document = docx.Document()
    document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def rezip(data: bytes, extra: dict[str, bytes] | None = None, edit=None) -> bytes:  # type: ignore[no-untyped-def]
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            payload = src.read(info)
            if edit is not None:
                payload = edit(info.filename, payload)
            dst.writestr(info, payload)
        for name, payload in (extra or {}).items():
            dst.writestr(name, payload)
    return out.getvalue()


def test_docx_is_detected_by_content() -> None:
    result = sniff(make_docx(), "comments.docx")
    assert result.kind is FileKind.DOCX
    assert result.notes == []


def test_exe_renamed_to_docx_is_rejected() -> None:
    with pytest.raises(IngestError, match="не поддерживается"):
        sniff(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100, "report.docx")


def test_legacy_or_password_protected_office_is_rejected() -> None:
    with pytest.raises(IngestError, match="Старый формат"):
        sniff(bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 100, "old.doc")


def test_plain_zip_that_is_not_office_is_rejected() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(IngestError, match="не документ Office"):
        sniff(out.getvalue(), "fake.xlsx")


def test_wrong_extension_is_read_by_content_with_a_note() -> None:
    result = sniff(make_docx(), "table.xlsx")
    assert result.kind is FileKind.DOCX
    assert "не совпадает" in result.notes[0].message


def test_text_files_need_a_text_extension_and_utf8() -> None:
    assert sniff("Суперсервис — это…".encode(), "superservice.txt").kind is FileKind.TEXT
    with pytest.raises(IngestError):
        sniff(b"\xff\xfe\x00binary", "data.txt")
    with pytest.raises(IngestError):
        sniff(b"plain text", "script.sh")


def test_path_traversal_entry_is_rejected() -> None:
    data = rezip(make_docx(), extra={"../../etc/passwd": b"x"})
    with pytest.raises(IngestError, match="недопустимые пути"):
        sniff(data, "evil.docx")


def test_zip_bomb_is_rejected_by_compression_ratio() -> None:
    data = rezip(make_docx())
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info))
        dst.writestr("word/media/bomb.bin", b"\x00" * (5 * 1024 * 1024), zipfile.ZIP_DEFLATED)
    with pytest.raises(IngestError, match="архивную бомбу"):
        sniff(out.getvalue(), "bomb.docx")


def test_too_many_entries_is_rejected() -> None:
    extra = {f"word/media/{i}.xml": b"<a/>" for i in range(MAX_ENTRIES + 1)}
    with pytest.raises(IngestError, match="слишком много"):
        sniff(rezip(make_docx(), extra=extra), "many.docx")


def test_macros_are_removed_and_the_document_still_opens() -> None:
    def to_docm(name: str, payload: bytes) -> bytes:
        if name == "[Content_Types].xml":
            return payload.replace(DOCX_CT.encode(), DOCM_CT.encode())
        if name == "word/_rels/document.xml.rels":
            rel = f'<Relationship Id="rIdVba" Type="{VBA_REL}" Target="vbaProject.bin"/>'
            return payload.replace(b"</Relationships>", rel.encode() + b"</Relationships>")
        return payload

    docm = rezip(make_docx("С макросом"), extra={"word/vbaProject.bin": b"VBA!"}, edit=to_docm)
    result = sniff(docm, "with_macro.docm")
    assert result.kind is FileKind.DOCX
    assert any("макросы" in note.message for note in result.notes)
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        names = archive.namelist()
        rels = archive.read("word/_rels/document.xml.rels")
    assert "word/vbaProject.bin" not in names
    assert b"vbaProject" not in rels
    assert docx.Document(io.BytesIO(result.data)).paragraphs[0].text == "С макросом"
