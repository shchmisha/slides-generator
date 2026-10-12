"""Scenario + facts → render-ready deck spec, or the full list of problems.

All errors are collected (not just the first) and phrased for the model, so one repair call
can fix them all. Resolution works on a copy of the fact store: names from one scenario never
leak into the next attempt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.facts.format import format_number, format_quantity
from app.facts.models import Derived, NameRef, Quantity, ScaleOp, Series
from app.facts.ops import evaluate
from app.facts.store import FactError, FactStore, normalize_label
from app.scenario import deck as sc
from app.scenario import render as rd
from app.scenario.text import render_text, series_item

DEFAULT_COLORS: tuple[rd.ColorToken, ...] = ("primary", "accent", "muted", "grid")
CHANGE_NEUTRAL_PCT = 3.0  # smaller changes are shown without a red/green tone


class ScenarioError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class ClaimCheck:
    source: str
    quote: str
    label: str
    value: str  # formatted figure from the data
    matches: bool


@dataclass
class ResolvedDeck:
    spec: rd.DeckSpec
    claims: list[ClaimCheck]
    derived: list[Derived]
    warnings: list[str] = field(default_factory=list)


def resolve_deck(scenario: sc.DeckScenario, facts: FactStore) -> ResolvedDeck:
    store = FactStore(facts.tables)
    errors: list[str] = []
    warnings: list[str] = []

    for name, ref in scenario.values.items():
        try:
            result = store.resolve(ref)
            label = result.label
            store.add_derived(Derived(id=name, label=label, op="ref", result=result))
        except FactError as exc:
            errors.append(f"values.{name}: {exc}")
    for i, op in enumerate(scenario.derived):
        try:
            derived = evaluate(op, store)
            warnings += derived.warnings
        except FactError as exc:
            errors.append(f"derived[{i}] ({op.id}): {exc}")

    constants = frozenset(f"{op.factor:g}" for op in scenario.derived if isinstance(op, ScaleOp))
    resolver = _SlideResolver(store, errors, warnings, constants)
    slides = [resolver.slide(slide, f"slides[{i}]") for i, slide in enumerate(scenario.slides)]
    claims = [resolver.claim(claim, f"claims[{i}]") for i, claim in enumerate(scenario.claims)]
    footer = resolver.text(scenario.footer, "footer")
    title = resolver.text(scenario.title, "title") or ""
    if errors:
        raise ScenarioError(errors)
    spec = rd.DeckSpec(title=title, footer=footer, slides=[s for s in slides if s is not None])
    return ResolvedDeck(
        spec=spec,
        claims=[c for c in claims if c is not None],
        derived=store.derived,
        warnings=warnings,
    )


def excel_format(decimals: int, unit: str | None) -> str:
    """Number format for chart axes and labels: "#,##0", "#,##0.0", '0.0"%"'."""
    fraction = "." + "0" * decimals if decimals else ""
    if unit == "%":
        return f'0{fraction}"%"'
    return f"#,##0{fraction}"


class _SlideResolver:
    def __init__(
        self, store: FactStore, errors: list[str], warnings: list[str], constants: frozenset[str]
    ) -> None:
        self.store = store
        self.errors = errors
        self.warnings = warnings
        self.constants = constants

    # ------------------------------------------------------------------------- helpers

    def text(self, value: str | None, path: str) -> str | None:
        if not value:
            return None
        return render_text(value, self.store, path, self.errors, self.constants)

    def lookup(self, name: str, path: str) -> Quantity | Series | None:
        try:
            return self.store.resolve(NameRef(name=name))
        except FactError as exc:
            self.errors.append(f"{path}: {exc}")
            return None

    def scalar(self, name: str, path: str) -> Quantity | None:
        value = self.lookup(name, path)
        if isinstance(value, Series):
            self.errors.append(f"{path}: «{name}» — ряд, а нужно одно значение")
            return None
        return value

    def item(self, name: str, key: str | None, path: str) -> Quantity | None:
        """A single value, or one item of a series when `key` is given."""
        if key is None:
            return self.scalar(name, path)
        data = self.series(name, path)
        if data is None:
            return None
        found = series_item(data, key)
        if found is None:
            keys = ", ".join(i.key for i in data.items)
            self.errors.append(f"{path}: в «{name}» нет «{key}». Есть: {keys}")
        return found

    def series(self, name: str, path: str) -> Series | None:
        value = self.lookup(name, path)
        if isinstance(value, Quantity):
            self.errors.append(
                f"{path}: «{name}» — одно значение, а нужен ряд (строка или столбец)"
            )
            return None
        return value

    def keys(self, first: Series, wanted: list[str] | None, path: str) -> list[str]:
        if wanted is None:
            return [item.key for item in first.items]
        known = {normalize_label(i.key): i.key for i in first.items}
        keys = []
        for key in wanted:
            if normalize_label(key) in known:
                keys.append(known[normalize_label(key)])
            else:
                self.errors.append(
                    f"{path}: в «{first.label}» нет «{key}». Есть: {', '.join(known.values())}"
                )
        return keys

    # -------------------------------------------------------------------------- slides

    def slide(self, slide: sc.Slide, path: str) -> rd.SlideSpec | None:
        match slide:
            case sc.KeyTakeaways():
                return self.key_takeaways(slide, path)
            case sc.ChartTable():
                chart = self.chart(slide.chart, f"{path}.chart")
                table = self.table(slide.table, f"{path}.table")
                if chart is None or table is None:
                    return None
                return rd.ChartTableSlide(
                    title=self.text(slide.title, f"{path}.title") or "",
                    subtitle=self.text(slide.subtitle, f"{path}.subtitle"),
                    chart=chart,
                    table=table,
                    callout=self.text(slide.callout, f"{path}.callout"),
                )
            case sc.ChartOnly():
                chart = self.chart(slide.chart, f"{path}.chart")
                if chart is None:
                    return None
                return rd.ChartSlide(
                    title=self.text(slide.title, f"{path}.title") or "",
                    subtitle=self.text(slide.subtitle, f"{path}.subtitle"),
                    chart=chart,
                    callout=self.text(slide.callout, f"{path}.callout"),
                )
            case sc.TableOnly():
                table = self.table(slide.table, f"{path}.table")
                if table is None:
                    return None
                return rd.TableSlide(
                    title=self.text(slide.title, f"{path}.title") or "",
                    subtitle=self.text(slide.subtitle, f"{path}.subtitle"),
                    table=table,
                    callout=self.text(slide.callout, f"{path}.callout"),
                )
            case sc.CostStructure():
                return self.cost_structure(slide, path)
            case sc.RisksDecisions():
                return rd.RisksDecisionsSlide(
                    title=self.text(slide.title, f"{path}.title") or "",
                    subtitle=self.text(slide.subtitle, f"{path}.subtitle"),
                    risks=self.points(slide.risks, f"{path}.risks"),
                    decisions=self.points(slide.decisions, f"{path}.decisions"),
                )

    def points(self, points: list[sc.Point], path: str) -> list[rd.Point]:
        return [
            rd.Point(
                title=self.text(p.title, f"{path}[{i}].title") or "",
                text=self.text(p.text, f"{path}[{i}].text") or "",
            )
            for i, p in enumerate(points)
        ]

    def key_takeaways(self, slide: sc.KeyTakeaways, path: str) -> rd.KeyTakeawaysSlide:
        kpis = []
        for i, kpi in enumerate(slide.kpis):
            q = self.item(kpi.value, kpi.key, f"{path}.kpis[{i}].value")
            value = (
                format_number(q.value, q.decimals) if q and q.value is not None else "нет данных"
            )
            kpis.append(
                rd.Kpi(
                    label=self.text(kpi.label, f"{path}.kpis[{i}].label") or "",
                    value=value,
                    unit=q.unit if q and q.value is not None else None,
                    delta=self.text(kpi.delta, f"{path}.kpis[{i}].delta"),
                    tone=kpi.tone,
                )
            )
        return rd.KeyTakeawaysSlide(
            title=self.text(slide.title, f"{path}.title") or "",
            subtitle=self.text(slide.subtitle, f"{path}.subtitle"),
            kpis=kpis,
            takeaways=[
                self.text(t, f"{path}.takeaways[{i}]") or "" for i, t in enumerate(slide.takeaways)
            ],
        )

    def chart(self, chart: sc.ChartScenario, path: str) -> rd.ChartSpec | None:
        resolved = [
            self.series(s.value, f"{path}.series[{i}].value") for i, s in enumerate(chart.series)
        ]
        if any(s is None for s in resolved):
            return None
        series = [s for s in resolved if s is not None]
        categories = self.keys(series[0], chart.categories, f"{path}.categories")
        out: list[rd.ChartSeries] = []
        formats: dict[str, str] = {}
        for i, (spec, data) in enumerate(zip(chart.series, series, strict=True)):
            values: list[float | None] = []
            decimals = 0
            for key, item in zip(categories, aligned(data, series[0], categories), strict=True):
                if item is None:
                    self.warnings.append(f"{path}.series[{i}]: нет «{key}» — пропуск на графике")
                values.append(
                    round(item.value, item.decimals) if item and item.value is not None else None
                )
                decimals = max(decimals, item.decimals if item else 0)
            formats.setdefault(spec.axis, excel_format(decimals, data.unit))
            out.append(
                rd.ChartSeries(
                    name=self.text(spec.name, f"{path}.series[{i}].name") or "",
                    values=values,
                    kind=spec.kind,
                    axis=spec.axis,
                    color=spec.color or DEFAULT_COLORS[i % len(DEFAULT_COLORS)],
                    show_values=spec.show_values,
                )
            )
        if all(s.axis == "secondary" for s in out):
            self.errors.append(f"{path}: хотя бы один ряд должен быть на основной оси")
        if any(s.kind == "bar" and s.axis == "secondary" for s in out):
            self.errors.append(f"{path}: на вспомогательной оси могут быть только линии")
        return rd.ChartSpec(
            caption=self.text(chart.caption, f"{path}.caption"),
            categories=display_keys(categories),
            series=out,
            value_format=formats.get("primary", "#,##0"),
            secondary_format=formats.get("secondary"),
        )

    def table(self, table: sc.TableScenario, path: str) -> rd.TableSpec | None:
        resolved = [
            self.series(c.value, f"{path}.columns[{i}].value") for i, c in enumerate(table.columns)
        ]
        if any(s is None for s in resolved):
            return None
        columns = [s for s in resolved if s is not None]
        keys = self.keys(columns[0], table.rows, f"{path}.rows")

        cells: list[list[rd.TableCell]] = [[rd.TableCell(text=k)] for k in display_keys(keys)]
        for spec, data in zip(table.columns, columns, strict=True):
            quantities = aligned(data, columns[0], keys)
            values = [q.value for q in quantities if q is not None and q.value is not None]
            marked = None
            if spec.highlight and values:
                marked = min(values) if spec.highlight == "lowest" else max(values)
            for row, q in zip(cells, quantities, strict=True):
                row.append(_cell(q, spec, marked))

        has_total = any(c.total for c in table.columns)
        if has_total:
            total_row = [rd.TableCell(text=table.total_label)]
            for i, spec in enumerate(table.columns):
                q = self.scalar(spec.total, f"{path}.columns[{i}].total") if spec.total else None
                total_row.append(_cell(q, spec, None) if q else rd.TableCell(text=""))
            cells.append(total_row)

        return rd.TableSpec(
            columns=[rd.TableColumn(header=table.row_header, align="left", width=1.4)]
            + [rd.TableColumn(header=c.header, align="right") for c in table.columns],
            rows=cells,
            total_row=has_total,
        )

    def cost_structure(self, slide: sc.CostStructure, path: str) -> rd.CostStructureSlide | None:
        amounts = self.series(slide.amounts, f"{path}.amounts")
        change = self.series(slide.change, f"{path}.change") if slide.change else None
        if amounts is None:
            return None
        items = [i for i in amounts.items if i.quantity.value is not None]
        if len(items) != len(amounts.items):
            self.warnings.append(f"{path}: статьи без данных не показаны")
        total = sum(i.quantity.value or 0 for i in items)
        if total <= 0:
            self.errors.append(f"{path}.amounts: сумма «{amounts.label}» не положительна")
            return None
        decimals = max((i.quantity.decimals for i in items), default=0)
        out = []
        for item in sorted(items, key=lambda i: i.quantity.value or 0, reverse=True):
            share = (item.quantity.value or 0) / total * 100
            delta = series_item(change, item.key) if change else None
            out.append(
                rd.CostItem(
                    label=item.key,
                    value=item.quantity.formatted(),
                    share=round(share, 2),
                    share_text=format_quantity(share, "%", 1),
                    change=delta.formatted(signed=True)
                    if delta and delta.value is not None
                    else None,
                    change_tone=_change_tone(delta, slide.increase_is) if delta else None,
                )
            )
        return rd.CostStructureSlide(
            title=self.text(slide.title, f"{path}.title") or "",
            subtitle=self.text(slide.subtitle, f"{path}.subtitle"),
            total=rd.CostTotal(
                label=slide.total_label, value=format_quantity(total, amounts.unit, decimals)
            ),
            items=out,
            callout=self.text(slide.callout, f"{path}.callout"),
        )

    def claim(self, claim: sc.Claim, path: str) -> ClaimCheck | None:
        q = self.scalar(claim.value, f"{path}.value")
        if q is None:
            return None
        stated = [float(t.replace(",", ".")) for t in re.findall(r"\d+(?:[.,]\d+)?", claim.quote)]
        matches = q.value is not None and any(
            abs(n - q.value) <= max(0.5, abs(q.value) * 0.01) for n in stated
        )
        return ClaimCheck(
            source=claim.source,
            quote=claim.quote,
            label=q.label,
            value=q.formatted(),
            matches=matches,
        )


def aligned(data: Series, reference: Series, keys: list[str]) -> list[Quantity | None]:
    """Values of `data` for the keys of `reference`: by label, or by position when the labels
    differ but the lengths match (a "Июль факт" row next to a "Июль" row of another table)."""
    by_label = [series_item(data, key) for key in keys]
    if any(q is not None for q in by_label) or len(data.items) != len(reference.items):
        return by_label
    index = {normalize_label(item.key): i for i, item in enumerate(reference.items)}
    return [data.items[index[normalize_label(key)]].quantity for key in keys]


def display_keys(keys: list[str]) -> list[str]:
    """Drop a trailing word every label shares: "Июль факт", "Август факт" → "Июль", "Август"."""
    words = [key.split() for key in keys]
    if (
        len(keys) > 1
        and all(len(w) > 1 for w in words)
        and len({w[-1].casefold() for w in words}) == 1
    ):
        return [" ".join(w[:-1]) for w in words]
    return keys


def _cell(q: Quantity | None, spec: sc.TableColumnSpec, marked: float | None) -> rd.TableCell:
    if q is None or q.value is None:
        return rd.TableCell(text="нет данных", tone="neutral")
    tone: rd.Tone | None = None
    if spec.tone_by_sign and round(q.value, q.decimals) != 0:
        tone = "positive" if q.value > 0 else "negative"
    bold = marked is not None and q.value == marked
    if bold:
        tone = "negative"
    return rd.TableCell(
        text=format_number(q.value, q.decimals, signed=spec.signed), tone=tone, bold=bold
    )


def _change_tone(delta: Quantity, increase_is: str) -> rd.Tone:
    if delta.value is None or abs(delta.value) < CHANGE_NEUTRAL_PCT:
        return "neutral"
    good = (delta.value > 0) == (increase_is == "good")
    return "positive" if good else "negative"
