"""Content-based file type detection and container safety checks.

The extension is never trusted: an OOXML document is a zip whose [Content_Types].xml names the
main part. Everything is read in memory, nothing is unpacked to disk. Macro-enabled documents
are accepted, but their VBA parts are removed before any library opens the file.
"""

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from defusedxml import ElementTree

from app.ingest.errors import IngestError
from app.ingest.models import FileKind, Note

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")  # legacy .doc/.xls/.ppt and password-protected OOXML
TEXT_EXTENSIONS = (".txt", ".md")

MAX_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
RATIO_CHECK_MIN_BYTES = 1024 * 1024
MAX_CONTENT_TYPES_BYTES = 1024 * 1024

CT_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_MACRO_TO_PLAIN = {
    "application/vnd.ms-word.document.macroEnabled.main+xml": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    ),
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    ),
    "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
    ),
}
_MAIN_CONTENT_TYPES = {
    _MACRO_TO_PLAIN["application/vnd.ms-word.document.macroEnabled.main+xml"]: FileKind.DOCX,
    _MACRO_TO_PLAIN["application/vnd.ms-excel.sheet.macroEnabled.main+xml"]: FileKind.XLSX,
    _MACRO_TO_PLAIN["application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"]: (
        FileKind.PPTX
    ),
}
_EXTENSION_KIND = {
    ".docx": FileKind.DOCX,
    ".docm": FileKind.DOCX,
    ".xlsx": FileKind.XLSX,
    ".xlsm": FileKind.XLSX,
    ".pptx": FileKind.PPTX,
    ".pptm": FileKind.PPTX,
}
_KIND_NAMES = {
    FileKind.DOCX: "документ Word",
    FileKind.XLSX: "таблица Excel",
    FileKind.PPTX: "презентация PowerPoint",
}
_VBA_RELATIONSHIP = re.compile(
    rb"<Relationship\b[^>]*Type=\"[^\"]*/(?:vbaProject|wordVbaData)\"[^>]*/>"
)


@dataclass(frozen=True)
class Sniffed:
    kind: FileKind
    data: bytes  # the bytes to parse: macros already removed
    notes: list[Note] = field(default_factory=list)


def sniff(data: bytes, filename: str) -> Sniffed:
    if not data:
        raise IngestError("Файл пустой")
    if data.startswith(ZIP_MAGIC):
        return _sniff_ooxml(data, filename)
    if data.startswith(OLE_MAGIC):
        raise IngestError(
            "Старый формат Office (.doc, .xls, .ppt) или файл с паролем не поддерживается. "
            "Сохраните файл как .docx, .xlsx или .pptx без пароля"
        )
    if filename.lower().endswith(TEXT_EXTENSIONS) and _is_utf8_text(data):
        return Sniffed(FileKind.TEXT, data)
    raise IngestError("Тип файла не поддерживается: загрузите .docx, .xlsx, .pptx, .txt или .md")


def _is_utf8_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _sniff_ooxml(data: bytes, filename: str) -> Sniffed:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise IngestError("Файл повреждён и не читается") from exc
    with archive:
        infos = archive.infolist()
        _check_container(infos)
        content_types = _content_types(archive)
        has_vba = any(_is_vba_part(info.filename) for info in infos)

    macro_types = content_types & _MACRO_TO_PLAIN.keys()
    plain_types = {_MACRO_TO_PLAIN.get(ct, ct) for ct in content_types}
    kinds = {_MAIN_CONTENT_TYPES[ct] for ct in plain_types if ct in _MAIN_CONTENT_TYPES}
    if len(kinds) != 1:
        raise IngestError("Это не документ Word, таблица Excel или презентация PowerPoint")
    kind = kinds.pop()

    notes: list[Note] = []
    expected = _EXTENSION_KIND.get(PurePosixPath(filename.lower()).suffix)
    if expected is not kind:
        notes.append(
            Note(
                severity="info",
                source=filename,
                message="Расширение файла не совпадает с содержимым: "
                f"прочитан как {_KIND_NAMES[kind]}",
            )
        )
    if macro_types or has_vba:
        data = _strip_macros(data)
        notes.append(
            Note(
                severity="info",
                source=filename,
                message="В файле были макросы: они не выполнялись и удалены перед чтением",
            )
        )
    return Sniffed(kind, data, notes)


def _check_container(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_ENTRIES:
        raise IngestError("В файле слишком много вложенных частей")
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ":" in name or ".." in PurePosixPath(name).parts:
            raise IngestError("Файл содержит недопустимые пути")
        if info.flag_bits & 0x1:
            raise IngestError("Файл зашифрован: снимите пароль и загрузите снова")
        compressed = max(info.compress_size, 1)
        if (
            info.file_size > RATIO_CHECK_MIN_BYTES
            and info.file_size > MAX_COMPRESSION_RATIO * compressed
        ):
            raise IngestError("Файл похож на архивную бомбу: слишком сильно сжат")
        total += info.file_size
    if total > MAX_UNCOMPRESSED_BYTES:
        raise IngestError("После распаковки файл больше 100 МБ")


def _content_types(archive: zipfile.ZipFile) -> set[str]:
    try:
        info = archive.getinfo("[Content_Types].xml")
    except KeyError as exc:
        raise IngestError("Это не документ Office: нет описания содержимого") from exc
    if info.file_size > MAX_CONTENT_TYPES_BYTES:
        raise IngestError("Файл повреждён: описание содержимого слишком большое")
    try:
        root = ElementTree.fromstring(archive.read(info))
    except ElementTree.ParseError as exc:
        raise IngestError("Файл повреждён: описание содержимого не читается") from exc
    return {
        el.get("ContentType", "") for el in root.iter(f"{CT_NS}Override") if el.get("ContentType")
    }


def _is_vba_part(name: str) -> bool:
    base = PurePosixPath(name).name.lower()
    return base.startswith(("vbaproject", "vbadata"))


def _strip_macros(data: bytes) -> bytes:
    """Copy the package without VBA parts and with macro-free content types."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as src,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            if _is_vba_part(info.filename):
                continue
            payload = src.read(info)
            if info.filename == "[Content_Types].xml":
                for macro, plain in _MACRO_TO_PLAIN.items():
                    payload = payload.replace(macro.encode(), plain.encode())
            elif info.filename.endswith(".rels"):
                payload = _VBA_RELATIONSHIP.sub(b"", payload)
            dst.writestr(info, payload)
    return out.getvalue()
