"""Deck scenario: what the model writes and the user reviews and edits.

Numbers never appear as literals. `values` gives names to facts, `derived` computes missing
figures with the fixed operations, slides refer to names, and text inserts numbers through
placeholders: "{yoy}", "{completion[Урал]}", "{deviation[Урал]:+}" (with an explicit sign).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.facts.models import Operation, Ref

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=120)]
Text = Annotated[str, Field(min_length=1, max_length=320)]
Tone = Literal["positive", "negative", "neutral"]
SeriesColor = Literal["primary", "accent", "muted", "grid", "positive", "negative"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Kpi(_Model):
    label: ShortText
    value: Name
    key: ShortText | None = None  # one item of a series: value "completion", key "Урал"
    delta: Text | None = None  # text with placeholders: "{over_plan:+} к плану"
    tone: Tone = "neutral"


class KeyTakeaways(_Model):
    type: Literal["key_takeaways"]
    title: ShortText
    subtitle: ShortText | None = None
    kpis: list[Kpi] = Field(min_length=2, max_length=4)
    takeaways: list[Text] = Field(min_length=2, max_length=5)


class ChartSeriesSpec(_Model):
    name: ShortText  # legend label
    value: Name  # a series: row or column of a table, or a derived series
    kind: Literal["bar", "line"] = "bar"
    axis: Literal["primary", "secondary"] = "primary"
    color: SeriesColor | None = None
    show_values: bool = False


class ChartScenario(_Model):
    caption: ShortText | None = None
    series: list[ChartSeriesSpec] = Field(min_length=1, max_length=4)
    categories: list[ShortText] | None = None  # subset and order; default: all of the first series


class TableColumnSpec(_Model):
    header: ShortText
    value: Name  # a series over the table rows
    signed: bool = False  # "+29" / "−16"
    tone_by_sign: bool = False  # positive green, negative red
    highlight: Literal["lowest", "highest"] | None = None  # mark the worst cell red
    total: Name | None = None  # value for the total row


class TableScenario(_Model):
    row_header: ShortText
    columns: list[TableColumnSpec] = Field(min_length=1, max_length=6)
    rows: list[ShortText] | None = None  # subset and order; default: all of the first column
    total_label: ShortText = "Итого"


class ChartTable(_Model):
    type: Literal["chart_table"]
    title: ShortText
    subtitle: ShortText | None = None
    chart: ChartScenario
    table: TableScenario
    callout: Text | None = None


class ChartOnly(_Model):
    type: Literal["chart"]
    title: ShortText
    subtitle: ShortText | None = None
    chart: ChartScenario
    callout: Text | None = None


class TableOnly(_Model):
    type: Literal["table"]
    title: ShortText
    subtitle: ShortText | None = None
    table: TableScenario
    callout: Text | None = None


class CostStructure(_Model):
    type: Literal["cost_structure"]
    title: ShortText
    subtitle: ShortText | None = None
    amounts: Name  # a series: cost items and their amounts; shares are computed
    change: Name | None = None  # a series of % changes for the same items
    increase_is: Literal["bad", "good"] = "bad"
    total_label: ShortText = "Всего"
    callout: Text | None = None


class Point(_Model):
    title: ShortText
    text: Text


class RisksDecisions(_Model):
    type: Literal["risks_decisions"]
    title: ShortText
    subtitle: ShortText | None = None
    risks: list[Point] = Field(min_length=1, max_length=4)
    decisions: list[Point] = Field(min_length=1, max_length=4)


Slide = Annotated[
    KeyTakeaways | ChartTable | ChartOnly | TableOnly | CostStructure | RisksDecisions,
    Field(discriminator="type"),
]


class Claim(_Model):
    """A number stated in the documents, checked against the data (the table wins)."""

    source: ShortText  # file name
    quote: Text  # the statement as written: "рост к прошлому году составил около 12%"
    value: Name  # the figure from the data that checks it


class DeckScenario(_Model):
    kind: Literal["deck"]
    title: ShortText
    footer: ShortText | None = None
    values: dict[Name, Ref] = Field(default_factory=dict, max_length=80)
    derived: list[Operation] = Field(default_factory=list, max_length=40)
    slides: list[Slide] = Field(min_length=1, max_length=15)
    claims: list[Claim] = Field(default_factory=list, max_length=10)
