"""Render-ready deck spec: the input of the pptx-builder skill (skills/pptx-builder/src/types.ts).

Every number here is already a formatted string or a chart value taken from facts; nothing in
this structure comes from the model unchecked.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Tone = Literal["positive", "negative", "neutral"]
ColorToken = Literal[
    "primary", "accent", "text", "muted", "positive", "negative", "surface", "grid", "background"
]


class _Spec(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Kpi(_Spec):
    label: str
    value: str
    unit: str | None = None
    delta: str | None = None
    tone: Tone = "neutral"


class KeyTakeawaysSlide(_Spec):
    type: Literal["key_takeaways"] = "key_takeaways"
    title: str
    subtitle: str | None = None
    kpis: list[Kpi]
    takeaways: list[str]


class ChartSeries(_Spec):
    name: str
    values: list[float | None]
    kind: Literal["bar", "line"]
    axis: Literal["primary", "secondary"] = "primary"
    color: ColorToken
    show_values: bool = False


class ChartSpec(_Spec):
    caption: str | None = None
    categories: list[str]
    series: list[ChartSeries]
    value_format: str
    secondary_format: str | None = None
    secondary_min: float | None = None
    secondary_max: float | None = None


class TableColumn(_Spec):
    header: str
    align: Literal["left", "right", "center"]
    width: float | None = None


class TableCell(_Spec):
    text: str
    tone: Tone | None = None
    bold: bool = False


class TableSpec(_Spec):
    columns: list[TableColumn]
    rows: list[list[TableCell]]
    total_row: bool = False


class ChartTableSlide(_Spec):
    type: Literal["chart_table"] = "chart_table"
    title: str
    subtitle: str | None = None
    chart: ChartSpec
    table: TableSpec
    callout: str | None = None


class TableSlide(_Spec):
    type: Literal["table"] = "table"
    title: str
    subtitle: str | None = None
    table: TableSpec
    callout: str | None = None


class ChartSlide(_Spec):
    type: Literal["chart"] = "chart"
    title: str
    subtitle: str | None = None
    chart: ChartSpec
    callout: str | None = None


class CostTotal(_Spec):
    label: str
    value: str


class CostItem(_Spec):
    label: str
    value: str
    share: float
    share_text: str
    change: str | None = None
    change_tone: Tone | None = None


class CostStructureSlide(_Spec):
    type: Literal["cost_structure"] = "cost_structure"
    title: str
    subtitle: str | None = None
    total: CostTotal | None = None
    items: list[CostItem]
    callout: str | None = None


class Point(_Spec):
    title: str
    text: str


class RisksDecisionsSlide(_Spec):
    type: Literal["risks_decisions"] = "risks_decisions"
    title: str
    subtitle: str | None = None
    risks_title: str | None = None
    decisions_title: str | None = None
    risks: list[Point]
    decisions: list[Point]


SlideSpec = Annotated[
    KeyTakeawaysSlide
    | ChartTableSlide
    | TableSlide
    | ChartSlide
    | CostStructureSlide
    | RisksDecisionsSlide,
    Field(discriminator="type"),
]


class DeckSpec(_Spec):
    title: str
    footer: str | None = None
    slides: list[SlideSpec]

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, exclude_none=True)
