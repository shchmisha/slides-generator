"""Content-based file type detection and container safety checks.

The extension is never trusted: an OOXML document is a zip whose [Content_Types].xml names the
main part. Everything is read in memory, nothing is unpacked to disk. Macro-enabled documents
are accepted, but their VBA parts are removed before any library opens the file.
"""

import io
import posixpath
import shutil
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from xml.etree.ElementTree import Element
from xml.sax.saxutils import escape

from defusedxml import DefusedXmlException, ElementTree

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
MAX_RELS_BYTES = 4 * 1024 * 1024
# zipfile builds one object per central-directory record (46+ bytes each) before any of our
# checks run, so the directory size is bounded first, straight from the end-of-archive record.
MAX_DIRECTORY_BYTES = 1024 * 1024
ALLOWED_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
# Reading a damaged member raises any of these (bad CRC, truncated stream, unknown method).
_READ_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, OSError, ValueError)

# The very function ZipFile uses to find the end record (zip64 included). Private, but calling
# it is the only way to read exactly the numbers ZipFile will trust; tests pin its behaviour.
_end_record: Callable[[Any], list[Any] | None] = zipfile._EndRecData  # type: ignore[attr-defined]
_ECD_ENTRIES_TOTAL = 4
_ECD_SIZE = 5

CT_URI = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_URI = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = f"{{{CT_URI}}}"
_XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
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
# Parts that hold or point to macro code: the VBA project, its signatures, Word's VBA data,
# toolbar and key-map customisations. Matched in content types and relationship types.
_MACRO_MARKERS = ("vbaproject", "vbadata", "keymapcustomizations", "attachedtoolbars")


def _is_macro_type(value: str) -> bool:
    value = value.lower()
    return any(marker in value for marker in _MACRO_MARKERS)


@dataclass(frozen=True)
class _ContentTypes:
    overrides: dict[str, str]  # part name without the leading "/", lower case → content type
    defaults: dict[str, str]  # extension, lower case → content type

    def of(self, name: str) -> str:
        key = name.lower()
        base = key.rsplit("/", 1)[-1]
        extension = base.rsplit(".", 1)[1] if "." in base else ""  # OPC: after the last dot
        return self.overrides.get(key) or self.defaults.get(extension, "")


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
    _check_directory_size(data)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except _READ_ERRORS as exc:
        raise IngestError("Файл повреждён и не читается") from exc
    with archive:
        infos = archive.infolist()
        _check_container(infos)
        types = _content_types(archive)
        macro_parts = {info.filename for info in infos if _is_macro_part(info.filename, types)}

    content_types = set(types.overrides.values())
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
    if macro_types or macro_parts:
        data = _strip_macros(data, macro_parts)
        notes.append(
            Note(
                severity="info",
                source=filename,
                message="В файле были макросы: они не выполнялись и удалены перед чтением",
            )
        )
    return Sniffed(kind, data, notes)


def _check_directory_size(data: bytes) -> None:
    """Bound the central directory exactly as ZipFile will read it, before ZipFile reads it."""
    try:
        record = _end_record(io.BytesIO(data))
    except _READ_ERRORS as exc:
        raise IngestError("Файл повреждён и не читается") from exc
    if record is None:
        raise IngestError("Файл повреждён и не читается")
    if record[_ECD_ENTRIES_TOTAL] > MAX_ENTRIES or record[_ECD_SIZE] > MAX_DIRECTORY_BYTES:
        raise IngestError("В файле слишком много вложенных частей")


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
        if info.compress_type not in ALLOWED_COMPRESSION:
            raise IngestError("Файл сжат нестандартным способом и не читается")
        compressed = max(info.compress_size, 1)
        if (
            info.file_size > RATIO_CHECK_MIN_BYTES
            and info.file_size > MAX_COMPRESSION_RATIO * compressed
        ):
            raise IngestError("Файл похож на архивную бомбу: слишком сильно сжат")
        total += info.file_size
    if total > MAX_UNCOMPRESSED_BYTES:
        raise IngestError("После распаковки файл больше 100 МБ")


def _content_types(archive: zipfile.ZipFile) -> _ContentTypes:
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
    except _READ_ERRORS as exc:
        raise IngestError("Файл повреждён и не читается") from exc
    overrides = {
        el.get("PartName", "").lstrip("/").lower(): el.get("ContentType", "")
        for el in root.iter(f"{CT_NS}Override")
        if el.get("ContentType")
    }
    defaults = {
        el.get("Extension", "").lower(): el.get("ContentType", "")
        for el in root.iter(f"{CT_NS}Default")
        if el.get("ContentType")
    }
    return _ContentTypes(overrides, defaults)


def _is_macro_part(name: str, types: _ContentTypes) -> bool:
    """A macro part by its declared content type or, failing that, by its usual name."""
    base = PurePosixPath(name).name.lower()
    return base.startswith(_MACRO_MARKERS) or _is_macro_type(types.of(name))


def _parse_flat(payload: bytes, uri: str, root_name: str) -> Element:
    """[Content_Types].xml and .rels: one root element with attribute-only children."""
    try:
        root = ElementTree.fromstring(payload)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise IngestError("Файл повреждён: служебная часть не читается") from exc
    if root.tag != f"{{{uri}}}{root_name}":
        raise IngestError("Файл повреждён: служебная часть не читается")
    return root


def _write_flat(root: Element, uri: str, root_name: str) -> bytes:
    """Write the flat structure back. The stdlib serializer cannot keep a default namespace
    with plain attributes, and these two formats have nothing else to keep (OPC forbids
    extensions in them)."""
    entities = {'"': "&quot;", "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"}
    lines = [_XML_DECLARATION, f'<{root_name} xmlns="{uri}">']
    prefix = f"{{{uri}}}"
    for child in root:
        if not isinstance(child.tag, str) or not child.tag.startswith(prefix):
            continue
        attrs = "".join(
            f' {key}="{escape(value, entities)}"'
            for key, value in child.attrib.items()
            if not key.startswith("{")
        )
        lines.append(f"<{child.tag[len(prefix) :]}{attrs}/>")
    lines.append(f"</{root_name}>")
    return "".join(lines).encode("utf-8")


def _clean_content_types(payload: bytes) -> bytes:
    root = _parse_flat(payload, CT_URI, "Types")
    changed = False
    for child in list(root):
        content_type = child.get("ContentType", "")
        if _is_macro_type(content_type):
            root.remove(child)
            changed = True
        elif content_type in _MACRO_TO_PLAIN:
            child.set("ContentType", _MACRO_TO_PLAIN[content_type])
            changed = True
    return _write_flat(root, CT_URI, "Types") if changed else payload


def _clean_relationships(name: str, payload: bytes, removed: set[str]) -> bytes:
    """Drop relationships of a macro type or pointing to a removed part."""
    root = _parse_flat(payload, REL_URI, "Relationships")
    # "word/_rels/document.xml.rels" describes "word/document.xml": targets are relative to "word".
    source_dir = posixpath.dirname(posixpath.dirname(name))
    changed = False
    for child in list(root):
        target = child.get("Target", "")
        internal = child.get("TargetMode", "Internal") != "External"
        resolved = posixpath.normpath(
            target[1:] if target.startswith("/") else posixpath.join(source_dir, target)
        ).lower()
        if _is_macro_type(child.get("Type", "")) or (internal and resolved in removed):
            root.remove(child)
            changed = True
    return _write_flat(root, REL_URI, "Relationships") if changed else payload


def _strip_macros(data: bytes, macro_parts: set[str]) -> bytes:
    """Copy the package without macro parts and with macro-free content types.

    Members are streamed one buffer at a time; only the small XML parts that are edited are
    read whole, so a large picture does not sit in memory twice. The XML is parsed, not
    pattern-matched: regular expressions over hostile markup can take quadratic time.
    """
    out = io.BytesIO()
    removed = {name.lower() for name in macro_parts}
    try:
        with (
            zipfile.ZipFile(io.BytesIO(data)) as src,
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst,
        ):
            for info in src.infolist():
                if info.filename in macro_parts:
                    continue
                target = zipfile.ZipInfo(info.filename, info.date_time)
                target.compress_type = zipfile.ZIP_DEFLATED
                if info.filename == "[Content_Types].xml":
                    dst.writestr(target, _clean_content_types(src.read(info)))
                elif info.filename.lower().endswith(".rels"):
                    if info.file_size > MAX_RELS_BYTES:
                        raise IngestError("Файл повреждён: список связей слишком большой")
                    payload = src.read(info)
                    dst.writestr(target, _clean_relationships(info.filename, payload, removed))
                else:
                    with src.open(info) as reader, dst.open(target, "w") as writer:
                        shutil.copyfileobj(reader, writer, 1024 * 1024)
    except IngestError:
        raise
    except _READ_ERRORS as exc:
        raise IngestError("Файл повреждён и не читается") from exc
    return out.getvalue()
