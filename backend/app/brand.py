"""Project branding: the theme for the deck (colors and fonts) and the logo.

A client's brand.json usually names only a few colors (the test client gives primary, accent,
text, background, positive, negative). The skill needs nine tokens, so the rest are mixed from
the given ones: a light tint of the primary color for panels, a mid tint for grid lines, a
grey between text and primary for secondary text. Values end up in CSS and in the PPTX, so
only #RRGGBB colors and plain font names are accepted.

The logo is decoded by Chromium during layout, so it is checked first: PNG only, at most 2 MB,
every chunk's checksum valid, and a sane size (a 100 000 × 100 000 image is a memory bomb even
when the file is tiny). Then Pillow decodes it fully and writes a fresh PNG: a file that only
looks right is refused at upload instead of breaking every later render, and text or animation
chunks are dropped.
"""

from __future__ import annotations

import io
import re
import struct
import unicodedata
import zlib
from typing import Annotated, Any

from PIL import Image
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

HEX = r"^#[0-9a-fA-F]{6}$"
MAX_FONT_CHARS = 64

Color = Annotated[str, StringConstraints(pattern=HEX)]


def _font_name(value: str) -> str:
    """Exactly the skill's rule (src/theme.ts: letters, digits, spaces, hyphens), after
    composing accents copied in decomposed form; anything else would fail the render."""
    name = unicodedata.normalize("NFC", value).strip()
    allowed = all(ch in " -" or unicodedata.category(ch)[0] in "LN" for ch in name)
    if not (0 < len(name) <= MAX_FONT_CHARS and allowed):
        raise ValueError("название шрифта: только буквы, цифры, пробелы и дефисы")
    return name


FontName = Annotated[str, AfterValidator(_font_name)]

DEFAULT_THEME: dict[str, Any] = {
    "name": "default",
    "colors": {
        "primary": "#2B4C7E",
        "accent": "#D9822B",
        "text": "#1F2328",
        "muted": "#5E6A78",
        "positive": "#2F7D4F",
        "negative": "#B3352B",
        "surface": "#F3F5F8",
        "grid": "#D7DCE3",
        "background": "#FFFFFF",
    },
    "fonts": {"heading": "Arial", "body": "Arial"},
}

MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_LOGO_SIDE = 4096
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class BrandError(ValueError):
    """Shown to the user as is."""


class BrandColors(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary: Color
    accent: Color | None = None
    text: Color | None = None
    background: Color | None = None
    positive: Color | None = None
    negative: Color | None = None
    muted: Color | None = None
    surface: Color | None = None
    grid: Color | None = None


class BrandFonts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heading: FontName | None = None
    body: FontName | None = None


class BrandIn(BaseModel):
    """The client's brand.json as is: unknown keys (company, note, logo) are ignored."""

    model_config = ConfigDict(extra="ignore")

    company: Annotated[str, StringConstraints(max_length=200)] | None = None
    colors: BrandColors
    fonts: BrandFonts = Field(default_factory=BrandFonts)


def _rgb(color: str) -> tuple[int, int, int]:
    value = int(color[1:], 16)
    return value >> 16, (value >> 8) & 0xFF, value & 0xFF


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0, min(255, c))):02X}" for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    """`t` of the way from color a to color b."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(
        (ra[0] + (rb[0] - ra[0]) * t, ra[1] + (rb[1] - ra[1]) * t, ra[2] + (rb[2] - ra[2]) * t)
    )


def _luminance(color: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def theme_from_brand(brand: BrandIn) -> tuple[dict[str, Any], list[str]]:
    """A full skill theme, plus readability warnings for the user."""
    defaults = DEFAULT_THEME["colors"]
    c = brand.colors
    primary = c.primary.upper()
    text = (c.text or defaults["text"]).upper()
    background = (c.background or defaults["background"]).upper()
    colors = {
        "primary": primary,
        "accent": (c.accent or primary).upper(),
        "text": text,
        "muted": (c.muted or mix(mix(text, primary, 0.5), background, 0.25)).upper(),
        "positive": (c.positive or defaults["positive"]).upper(),
        "negative": (c.negative or defaults["negative"]).upper(),
        "surface": (c.surface or mix(primary, background, 0.95)).upper(),
        "grid": (c.grid or mix(primary, background, 0.83)).upper(),
        "background": background,
    }
    fonts = {
        "heading": brand.fonts.heading or brand.fonts.body or DEFAULT_THEME["fonts"]["heading"],
        "body": brand.fonts.body or brand.fonts.heading or DEFAULT_THEME["fonts"]["body"],
    }
    name = re.sub(r"[^\w -]", "", brand.company or "brand")[:60] or "brand"
    warnings = []
    for token, minimum in (("text", 4.5), ("muted", 3.0), ("primary", 3.0)):
        ratio = contrast(colors[token], background)
        if ratio < minimum:
            warnings.append(
                f"Цвет {token} плохо читается на фоне: контраст {ratio:.1f}:1, "
                f"нужно не меньше {minimum:g}:1"
            )
    return {"name": name, "colors": colors, "fonts": fonts}, warnings


def check_logo(data: bytes) -> None:
    """Accept only a well-formed PNG of a reasonable size; raise BrandError otherwise."""
    if len(data) > MAX_LOGO_BYTES:
        raise BrandError("Логотип больше 2 МБ")
    if not data.startswith(PNG_SIGNATURE):
        raise BrandError("Логотип должен быть в формате PNG")
    offset = len(PNG_SIGNATURE)
    first = True
    while True:
        if offset + 8 > len(data):
            raise BrandError("Файл логотипа повреждён")
        length, kind = struct.unpack(">I4s", data[offset : offset + 8])
        end = offset + 8 + length + 4
        if end > len(data):
            raise BrandError("Файл логотипа повреждён")
        body = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : end])
        if zlib.crc32(kind + body) != crc:
            raise BrandError("Файл логотипа повреждён")
        if first:
            if kind != b"IHDR" or length != 13:
                raise BrandError("Файл логотипа повреждён")
            width, height = struct.unpack(">II", body[:8])
            if not (0 < width <= MAX_LOGO_SIDE and 0 < height <= MAX_LOGO_SIDE):
                raise BrandError(
                    f"Логотип слишком большой: не больше {MAX_LOGO_SIDE} точек по стороне"
                )
            first = False
        offset = end
        if kind == b"IEND":
            return


def normalise_logo(data: bytes) -> bytes:
    """check_logo, then decode the whole image and encode it again as a plain PNG."""
    check_logo(data)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG":
                raise BrandError("Логотип должен быть в формате PNG")
            image.load()  # the first frame only, for an animated PNG
            out = io.BytesIO()
            image.save(out, format="PNG", optimize=True)
    except BrandError:
        raise
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise BrandError("Логотип не читается: сохраните его заново как PNG") from exc
    return out.getvalue()
