"""Typed view of layout.json, the hand-off from the Chromium layout step to the PPTX emitter.

Every element carries its box in CSS px on the 1280×720 slide canvas and the styles the browser
actually computed, so the emitter never has to re-do layout. Unknown fields or element kinds are
rejected: if the Node side changes its output, the emitter fails loudly instead of guessing.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

LAYOUT_VERSION = 1

ColorToken = Literal[
    "primary",
    "accent",
    "text",
    "muted",
    "positive",
    "negative",
    "surface",
    "grid",
    "background",
]
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Fonts(_Model):
    heading: str
    body: str


class Theme(_Model):
    name: str
    colors: dict[ColorToken, HexColor]
    fonts: Fonts

    @field_validator("colors")
    @classmethod
    def _every_token_defined(cls, colors: dict[ColorToken, str]) -> dict[ColorToken, str]:
        missing = set(get_args(ColorToken)) - colors.keys()
        if missing:
            raise ValueError(f"theme is missing colors: {', '.join(sorted(missing))}")
        return colors

    def rgb(self, token: ColorToken) -> str:
        """Color as RRGGBB, the form python-pptx expects."""
        return self.colors[token].removeprefix("#").upper()


class Box(_Model):
    x: float
    y: float
    w: float
    h: float


class Rgba(_Model):
    hex: str = Field(pattern=r"^[0-9A-F]{6}$")
    alpha: float = Field(ge=0, le=1)


class Run(_Model):
    text: str
    font: str
    size_px: float
    bold: bool
    italic: bool
    color: Rgba


class Para(_Model):
    runs: list[Run]
    align: Literal["left", "right", "center", "justify"]
    line_height_px: float
    space_after_px: float


class TextElement(_Model):
    kind: Literal["text"]
    box: Box
    paras: list[Para]
    valign: Literal["top", "middle", "bottom"]


class Border(_Model):
    px: float
    color: Rgba


class ShapeElement(_Model):
    kind: Literal["shape"]
    box: Box
    fill: Rgba
    radius_px: float
    ellipse: bool
    border: Border | None


class ImageElement(_Model):
    kind: Literal["image"]
    box: Box
    src: str


class ChartSeries(_Model):
    name: str
    values: list[float]
    kind: Literal["bar", "line"]
    axis: Literal["primary", "secondary"] = "primary"
    color: ColorToken
    show_values: bool = False


class ChartSpec(_Model):
    caption: str | None = None
    categories: list[str] = Field(min_length=1)
    series: list[ChartSeries] = Field(min_length=1)
    value_format: str
    secondary_format: str | None = None
    secondary_min: float | None = None
    secondary_max: float | None = None

    @model_validator(mode="after")
    def _supported_combination(self) -> ChartSpec:
        for series in self.series:
            if len(series.values) != len(self.categories):
                raise ValueError(f"series {series.name!r} needs one value per category")
            if series.kind == "bar" and series.axis == "secondary":
                raise ValueError("only line series can use the secondary axis")
        if all(s.axis == "secondary" for s in self.series):
            raise ValueError("at least one series must use the primary axis")
        return self


class AxisScale(_Model):
    min: float
    max: float
    step: float = Field(gt=0)


class ChartScales(_Model):
    """Axis ranges computed by the layout step; the preview SVG uses the same ones."""

    primary: AxisScale
    secondary: AxisScale | None = None


class ChartElement(_Model):
    kind: Literal["chart"]
    box: Box
    spec: ChartSpec
    scales: ChartScales


class CellLine(_Model):
    px: float
    color: Rgba


class TableCell(_Model):
    text: str
    font: str
    size_px: float
    bold: bool
    color: Rgba
    fill: Rgba
    align: Literal["left", "right", "center"]
    pad_px: tuple[float, float, float, float]  # top, right, bottom, left
    border_top: CellLine
    border_bottom: CellLine


class TableElement(_Model):
    kind: Literal["table"]
    box: Box
    col_w: list[float] = Field(min_length=1)
    row_h: list[float] = Field(min_length=1)
    cells: list[list[TableCell]]

    @model_validator(mode="after")
    def _rectangular(self) -> TableElement:
        # Merged cells are not supported: every row has exactly one cell per column.
        if len(self.cells) != len(self.row_h):
            raise ValueError("table needs one height per row")
        if any(len(row) != len(self.col_w) for row in self.cells):
            raise ValueError("every table row needs one cell per column")
        return self


Element = Annotated[
    TextElement | ShapeElement | ImageElement | ChartElement | TableElement,
    Field(discriminator="kind"),
]


class Slide(_Model):
    index: int
    type: str
    elements: list[Element]


class SlideSize(_Model):
    width_px: int
    height_px: int


class Issue(_Model):
    slide: int
    element: str
    kind: Literal["overflow", "outside", "shrunk"]
    detail: str


class Layout(_Model):
    version: Literal[1]
    title: str
    slide_size: SlideSize
    theme: Theme
    slides: list[Slide]
    issues: list[Issue]
    previews: list[str]
    timings: dict[str, float]
