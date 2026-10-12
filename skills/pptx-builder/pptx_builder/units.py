"""CSS px (96 per inch, as Chromium lays out the slide) → PowerPoint units."""

from pptx.util import Emu, Length

from pptx_builder.layout import Box

EMU_PER_PX = 914_400 // 96  # 9525 EMU = 1 CSS px
PT_PER_PX = 0.75


def emu(px: float) -> Length:
    return Emu(round(px * EMU_PER_PX))


def pt_from_px(px: float) -> Length:
    """A length given in points (font sizes, line widths) for a size measured in px."""
    return Emu(round(px * PT_PER_PX * 12_700))


def rect(box: Box) -> tuple[Length, Length, Length, Length]:
    return emu(box.x), emu(box.y), emu(box.w), emu(box.h)
