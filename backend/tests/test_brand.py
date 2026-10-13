"""Brand files become a full theme; logos are checked before Chromium decodes them."""

import struct
import zlib

import pytest
from pydantic import ValidationError

from app.brand import BrandError, BrandIn, check_logo, contrast, theme_from_brand

CLIENT_BRAND = {
    "company": "Условная Компания",
    "note": "Вымышленный фирменный стиль для тестового задания",
    "colors": {
        "primary": "#1F3A5F",
        "accent": "#E8A33D",
        "text": "#1D1D1F",
        "background": "#FFFFFF",
        "positive": "#2F7D4F",
        "negative": "#B3352B",
    },
    "fonts": {"heading": "Georgia", "body": "Arial"},
    "logo": "logo.png",
}


def png(width: int = 2, height: int = 2, *, crc_ok: bool = True) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) ^ (0 if crc_ok else 1)
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(min(height, 4)))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_client_brand_becomes_a_full_theme() -> None:
    theme, warnings = theme_from_brand(BrandIn.model_validate(CLIENT_BRAND))
    colors = theme["colors"]
    assert set(colors) == {
        "primary",
        "accent",
        "text",
        "muted",
        "positive",
        "negative",
        "surface",
        "grid",
        "background",
    }
    assert colors["primary"] == "#1F3A5F" and colors["accent"] == "#E8A33D"
    # Derived tokens: a pale panel, a visible grid, secondary text that still reads.
    assert contrast(colors["surface"], colors["background"]) < 1.2
    assert 1.2 < contrast(colors["grid"], colors["background"]) < 2
    assert contrast(colors["muted"], colors["background"]) >= 4.5
    assert theme["fonts"] == {"heading": "Georgia", "body": "Arial"}
    assert theme["name"] == "Условная Компания"
    assert warnings == []


def test_only_primary_is_required() -> None:
    theme, _ = theme_from_brand(BrandIn.model_validate({"colors": {"primary": "#123456"}}))
    assert theme["colors"]["accent"] == "#123456"
    assert theme["fonts"] == {"heading": "Arial", "body": "Arial"}


def test_unreadable_colors_are_reported() -> None:
    brand = {"colors": {"primary": "#EEEEEE", "text": "#DDDDDD", "background": "#FFFFFF"}}
    _, warnings = theme_from_brand(BrandIn.model_validate(brand))
    assert any("text" in w for w in warnings) and any("primary" in w for w in warnings)


@pytest.mark.parametrize(
    "brand",
    [
        {"colors": {"primary": "red"}},
        {"colors": {"primary": "#12345G"}},
        {"colors": {"primary": "#123456;}"}},
        {"colors": {"primary": "#123456"}, "fonts": {"heading": "Arial'; } body {"}},
        {"colors": {"primary": "#123456"}, "fonts": {"body": "Arial_Narrow"}},
        {"fonts": {"heading": "Arial"}},
    ],
)
def test_anything_but_plain_colors_and_font_names_is_rejected(brand: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BrandIn.model_validate(brand)


def test_valid_png_logo_is_accepted() -> None:
    check_logo(png())


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"GIF89a" + b"\x00" * 20, "PNG"),
        (png(crc_ok=False), "повреждён"),
        (png()[:-6], "повреждён"),
        (png(50_000, 50_000), "слишком большой"),
        (png() + b"\x00" * (2 * 1024 * 1024), "2 МБ"),
    ],
)
def test_bad_logos_are_rejected(data: bytes, message: str) -> None:
    with pytest.raises(BrandError, match=message):
        check_logo(data)


def test_font_names_follow_exactly_the_skill_rule() -> None:
    decomposed = "Мой шрифт".replace("й", "и\u0306")  # as copied from some macOS apps
    brand = BrandIn.model_validate(
        {"colors": {"primary": "#123456"}, "fonts": {"heading": decomposed}}
    )
    assert brand.fonts.heading == "Мой шрифт"
    for bad in ("Font\u200dName", "Font\u203fName", "देवनागरी"):
        with pytest.raises(ValidationError):
            BrandIn.model_validate({"colors": {"primary": "#123456"}, "fonts": {"body": bad}})


def test_logo_that_does_not_decode_is_rejected() -> None:
    from app.brand import normalise_logo

    header_only = png()
    idat_start = header_only.index(b"IDAT") - 4
    iend = header_only.index(b"IEND") - 4
    no_pixels = header_only[:idat_start] + header_only[iend:]
    check_logo(no_pixels)  # structurally fine...
    with pytest.raises(BrandError, match="не читается"):
        normalise_logo(no_pixels)  # ...but there is no picture in it
    assert normalise_logo(png(8, 8)).startswith(b"\x89PNG")
