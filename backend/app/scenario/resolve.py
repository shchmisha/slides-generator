"""Scenario + facts → render-ready deck spec, or the full list of problems.

All errors are collected (not just the first) and phrased for the model, so one repair call
can fix them all. Resolution works on a copy of the fact store: names from one scenario never
leak into the next attempt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.facts.format import format_number, format_quantity, round_half_up
from app.facts.models import Derived, NameRef, Quantity, RateOp, Series
from app.facts.ops import evaluate
from app.facts.store import FactError, FactStore, normalize_label
from app.ingest.models import Note
from app.scenario import deck as sc
from app.scenario import render as rd
from app.scenario.text import render_text, series_item

# Scale words in a label the model writes: "Заказы, тыс. шт." over a column in "шт." claims a
# thousand times the real figure (seen live), so a label may only repeat the data's scale.
_SCALES = (
    # "на тысячу заказов" is a rate, not a scale.
    ("тыс", re.compile(r"(?<!на )\b(?:тыс\w*|тысяч\w*)", re.IGNORECASE)),
    ("млн", re.compile(r"\bмлн\b|\bмиллион\w*", re.IGNORECASE)),
    ("млрд", re.compile(r"\bмлрд\b|\bмиллиард\w*", re.IGNORECASE)),
)


# The period a label speaks of, and the period of the cell its value comes from: "Выручка за
# квартал" over "› Итого по компании › Сентябрь факт" (998, not 2 877) was seen live.
_MONTHS = (
    ("январь", "январ"),
    ("февраль", "феврал"),
    ("март", "март"),
    ("апрель", "апрел"),
    ("май", "ма[йя]"),
    ("июнь", "июн"),
    ("июль", "июл"),
    ("август", "август"),
    ("сентябрь", "сентябр"),
    ("октябрь", "октябр"),
    ("ноябрь", "ноябр"),
    ("декабрь", "декабр"),
)
_WHOLE_PERIOD = re.compile(r"квартал|за три месяца|за 3 месяца|итого", re.IGNORECASE)


def period_of(text: str) -> str | None:
    """ "сентябрь", "квартал", or None when the text names no period or several months."""
    months = {name for name, stem in _MONTHS if re.search(rf"\b{stem}", text, re.IGNORECASE)}
    if len(months) == 1:
        return months.pop()
    if not months and _WHOLE_PERIOD.search(text):
        return "квартал"
    return None


def scale_word(text: str | None) -> str | None:
    for word, pattern in _SCALES:
        if text and pattern.search(text):
            return word
    return None


DEFAULT_COLORS: tuple[rd.ColorToken, ...] = ("primary", "accent", "muted", "grid")
CHANGE_NEUTRAL_PCT = 3.0  # smaller changes are shown without a red/green tone
# A line whose values are this small next to the bars would be flat on their axis.
SECONDARY_AXIS_RATIO = 0.25
MAX_DATA_NOTES_ROWS = 8
_PER_N = re.compile(r"\bна\s?\d", re.IGNORECASE)  # "на 1000 заказов"
_VALUE_REF = re.compile(r"^([a-z][a-z0-9_]*)(?:\[(.+)\])?$")


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
    matches: bool | None  # None: the quote has no number to compare


@dataclass
class ResolvedDeck:
    spec: rd.DeckSpec
    claims: list[ClaimCheck]
    derived: list[Derived]
    warnings: list[str] = field(default_factory=list)
    # For each slide of `spec`: the index of the scenario slide it came from, None for the
    # «Проверка данных» slide added automatically (a data_notes slide with nothing to say is
    # left out, so the two lists can differ).
    origins: list[int | None] = field(default_factory=list)


def _names_in(value: object) -> set[str]:
    """Names referenced inside an operation: every {"name": …} at any depth."""
    if isinstance(value, dict):
        found = {value["name"]} if set(value) == {"name"} else set()
        return found.union(*(_names_in(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_names_in(v) for v in value))
    return set()


def _in_dependency_order(values: dict[str, Any]) -> list[tuple[str, Any]]:
    """A value that names another value comes after it. Postgres JSONB does not keep the order
    of object keys, so the order the model wrote them in is lost once the scenario is stored."""
    order: list[str] = []
    state: dict[str, str] = {}

    def visit(name: str) -> None:
        if state.get(name):  # done, or a cycle: resolving it reports the error
            return
        state[name] = "visiting"
        ref = values[name]
        if isinstance(ref, NameRef) and ref.name in values:
            visit(ref.name)
        state[name] = "done"
        order.append(name)

    for name in values:
        visit(name)
    return [(name, values[name]) for name in order]


def resolve_deck(
    scenario: sc.DeckScenario,
    facts: FactStore,
    notes: list[Note] | None = None,
    *,
    auto_data_notes: bool = True,
) -> ResolvedDeck:
    """`notes` (data fixes, gaps, quarantined instructions) fill the «Проверка данных» slide;
    with `auto_data_notes` the slide is appended when there is something to report and the
    scenario left it out."""
    store = FactStore(facts.tables)
    errors: list[str] = []
    warnings: list[str] = []
    # Which operations each name was built with: a claim must not be "proved" by a constant.
    lineage: dict[str, set[str]] = {name: {"ref"} for name in scenario.values}

    for name, ref in _in_dependency_order(scenario.values):
        try:
            result = store.resolve(ref)
            label = result.label
            store.add_derived(Derived(id=name, label=label, op="ref", result=result))
        except FactError as exc:
            errors.append(f"values.{name}: {exc}")
    for i, op in enumerate(scenario.derived):
        if op.op in ("scale", "ratio") and _PER_N.search(f"{op.label} {getattr(op, 'unit', '')}"):
            errors.append(
                f"derived[{i}] ({op.id}): показатель «на N …» считается только через "
                'rate: {"op": "rate", "a": <что считаем>, "b": <на что делим>, "per": N}'
            )
        used = _names_in(op.model_dump())
        lineage[op.id] = {op.op}.union(*(lineage.get(name, set()) for name in used))
        try:
            derived = evaluate(op, store)
            warnings += derived.warnings
        except FactError as exc:
            errors.append(f"derived[{i}] ({op.id}): {exc}")

    rate_bases = frozenset(str(op.per) for op in scenario.derived if isinstance(op, RateOp))
    resolver = _SlideResolver(store, errors, warnings, lineage, rate_bases)
    claims = [resolver.claim(claim, f"claims[{i}]") for i, claim in enumerate(scenario.claims)]
    resolver.claim_checks = [c for c in claims if c is not None]
    resolver.notes = notes or []
    slides = [resolver.slide(slide, f"slides[{i}]") for i, slide in enumerate(scenario.slides)]
    has_notes_slide = any(isinstance(slide, sc.DataNotes) for slide in scenario.slides)
    if auto_data_notes and not has_notes_slide:
        appended = resolver.data_notes(sc.DataNotes(type="data_notes"), "data_notes")
        if appended is not None:
            slides.append(appended)
    footer = resolver.text(scenario.footer, "footer")
    title = resolver.text(scenario.title, "title") or ""
    if errors:
        raise ScenarioError(errors)
    origins: list[int | None] = [i for i, s in enumerate(slides) if s is not None]
    if len(slides) > len(scenario.slides) and slides[-1] is not None:
        origins[-1] = None  # appended above
    spec = rd.DeckSpec(title=title, footer=footer, slides=[s for s in slides if s is not None])
    return ResolvedDeck(
        spec=spec,
        claims=resolver.claim_checks,
        derived=store.derived,
        warnings=warnings,
        origins=origins,
    )


def excel_format(decimals: int, unit: str | None) -> str:
    """Number format for chart axes and labels: "#,##0", "#,##0.0", '0.0"%"'."""
    fraction = "." + "0" * decimals if decimals else ""
    if unit == "%":
        return f'0{fraction}"%"'
    return f"#,##0{fraction}"


class _SlideResolver:
    def __init__(
        self,
        store: FactStore,
        errors: list[str],
        warnings: list[str],
        lineage: dict[str, set[str]],
        rate_bases: frozenset[str] = frozenset(),
    ) -> None:
        self.store = store
        self.errors = errors
        self.warnings = warnings
        self.lineage = lineage
        self.rate_bases = rate_bases
        self.claim_checks: list[ClaimCheck] = []
        self.notes: list[Note] = []

    # ------------------------------------------------------------------------- helpers

    def text(self, value: str | None, path: str) -> str | None:
        if not value:
            return None
        return render_text(value, self.store, path, self.errors, self.rate_bases)

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

    def total_of(self, ref: str, path: str) -> Quantity | None:
        """A table total: a value, an item ("completion[Итого по компании]"), or a series —
        then its own total row."""
        match = _VALUE_REF.match(ref)
        if match is None:
            self.errors.append(f"{path}: ожидается имя или имя[ключ]")
            return None
        name, key = match.group(1), match.group(2)
        if key is not None:
            return self.item(name, key, path)
        value = self.lookup(name, path)
        if isinstance(value, Series):
            total = next((i.quantity for i in value.items if i.total), None)
            if total is None:
                self.errors.append(
                    f"{path}: в ряду «{name}» нет итоговой строки — укажите итог отдельно"
                )
            return total
        return value

    def check_period(self, label: str, q: Quantity, path: str) -> None:
        said, actual = period_of(label), period_of(q.label)
        if said and actual and said != actual:
            self.errors.append(
                f"{path}: подпись «{label}» говорит о периоде «{said}», а значение "
                f"{q.formatted()} — за «{actual}» ({q.label}). Возьмите значение за нужный "
                "период или поправьте подпись"
            )

    def check_scale(self, label: str | None, data: Series, path: str) -> None:
        claimed = scale_word(label)
        if claimed is None or data.unit is None:
            return
        actual = scale_word(data.unit)
        if claimed != actual:
            self.errors.append(
                f"{path}: «{label}» — в данных единицы «{data.unit}», а в подписи другой "
                f"масштаб ({claimed}): цифры читались бы в тысячи раз иначе. Пиши единицы как "
                "в таблице или пересчитай значения операцией scale"
            )

    def series(self, name: str, path: str) -> Series | None:
        value = self.lookup(name, path)
        if isinstance(value, Quantity):
            self.errors.append(
                f"{path}: «{name}» — одно значение, а нужен ряд (строка или столбец)"
            )
            return None
        return value

    def keys(self, first: Series, wanted: list[str] | None, path: str) -> list[str]:
        if wanted is None:  # total rows are shown in the table's total row, not as categories
            return [item.key for item in first.items if not item.total]
        keys = []
        for key in wanted:
            found = series_item(first, key)
            match = next((i.key for i in first.items if i.quantity is found), None)
            if found is not None and match is not None:
                keys.append(match)
            else:
                known = ", ".join(i.key for i in first.items)
                self.errors.append(f"{path}: в «{first.label}» нет «{key}». Есть: {known}")
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
            case sc.DataNotes():
                return self.data_notes(slide, path) or rd.TableSlide(
                    title=self.text(slide.title, f"{path}.title") or "",
                    table=rd.TableSpec(
                        columns=[rd.TableColumn(header="Проверка", align="left")],
                        rows=[[rd.TableCell(text="Замечаний к данным нет")]],
                    ),
                )
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
            if q is not None:
                self.check_period(kpi.label, q, f"{path}.kpis[{i}]")
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
        series_formats: list[str] = []
        for i, (spec, data) in enumerate(zip(chart.series, series, strict=True)):
            self.check_scale(spec.name, data, f"{path}.series[{i}].name")
            values: list[float | None] = []
            decimals = 0
            for key, item in zip(categories, aligned(data, series[0], categories), strict=True):
                if item is None:
                    self.warnings.append(f"{path}.series[{i}]: нет «{key}» — пропуск на графике")
                values.append(
                    round_half_up(item.value, item.decimals)
                    if item and item.value is not None
                    else None
                )
                decimals = max(decimals, item.decimals if item else 0)
            series_formats.append(excel_format(decimals, data.unit))
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
        out = _move_small_lines(out, path, self.warnings)
        formats: dict[str, str] = {}
        for s, fmt in zip(out, series_formats, strict=True):
            formats.setdefault(s.axis, fmt)
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
        for i, (spec, data) in enumerate(zip(table.columns, columns, strict=True)):
            self.check_scale(spec.header, data, f"{path}.columns[{i}].header")

        cells: list[list[rd.TableCell]] = [[rd.TableCell(text=k)] for k in display_keys(keys)]
        for spec, data in zip(table.columns, columns, strict=True):
            quantities = aligned(data, columns[0], keys)
            values = [q.value for q in quantities if q is not None and q.value is not None]
            marked = None
            if spec.highlight and values:
                marked = min(values) if spec.highlight == "lowest" else max(values)
            for row, q in zip(cells, quantities, strict=True):
                row.append(_cell(q, spec, marked))

        # The total row: an explicit total name, else the series' own total item
        # (e.g. completion by region carries «Итого по компании» = 101,5 %).
        totals: list[Quantity | None] = []
        for i, (spec, data) in enumerate(zip(table.columns, columns, strict=True)):
            if spec.total:
                total = self.total_of(spec.total, f"{path}.columns[{i}].total")
                own = next((t.quantity for t in data.items if t.total), None)
                if (
                    total
                    and own
                    and total.value is not None
                    and own.value is not None
                    and abs(total.value - own.value) > 0.5 * 10 ** -max(own.decimals, 0)
                ):
                    self.errors.append(
                        f"{path}.columns[{i}].total: «{spec.total}» = {total.formatted()}, "
                        f"а итог этого столбца в данных — {own.formatted()} "
                        f"({own.label}). Уберите total: итог возьмётся из данных"
                    )
                totals.append(total)
            else:
                totals.append(next((t.quantity for t in data.items if t.total), None))
        # An automatic total only when all rows are shown: a company total under a subset of
        # regions would not add up. An explicit total is always shown.
        explicit = any(c.total for c in table.columns)
        automatic = table.rows is None and any(q is not None for q in totals)
        has_total = explicit or automatic
        if has_total:
            total_label = self.text(table.total_label, f"{path}.total_label") or ""
            total_row = [rd.TableCell(text=total_label)]
            for spec, q in zip(table.columns, totals, strict=True):
                total_row.append(_cell(q, spec, None) if q else rd.TableCell(text=""))
            cells.append(total_row)

        headers = [
            self.text(c.header, f"{path}.columns[{i}].header") or ""
            for i, c in enumerate(table.columns)
        ]
        row_header = self.text(table.row_header, f"{path}.row_header") or ""
        return rd.TableSpec(
            columns=[rd.TableColumn(header=row_header, align="left", width=1.4)]
            + [rd.TableColumn(header=h, align="right") for h in headers],
            rows=cells,
            total_row=has_total,
        )

    def cost_structure(self, slide: sc.CostStructure, path: str) -> rd.CostStructureSlide | None:
        amounts = self.series(slide.amounts, f"{path}.amounts")
        change = self.series(slide.change, f"{path}.change") if slide.change else None
        change_label = change.label if change is not None else None
        if amounts is None:
            return None
        parts = [i for i in amounts.items if not i.total]
        items = [i for i in parts if i.quantity.value is not None]
        if len(items) != len(parts):
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
                label=self.text(slide.total_label, f"{path}.total_label") or "",
                value=format_quantity(total, amounts.unit, decimals),
            ),
            items=out,
            change_label=change_label,
            callout=self.text(slide.callout, f"{path}.callout"),
        )

    def claim(self, claim: sc.Claim, path: str) -> ClaimCheck | None:
        ref = _VALUE_REF.match(claim.value)
        if ref is None:
            self.errors.append(f"{path}.value: ожидается имя или имя[ключ]")
            return None
        name, key = ref.group(1), ref.group(2)
        if "scale" in self.lineage.get(name, set()):
            self.errors.append(
                f"{path}.value: утверждения проверяются только данными таблиц и расчётами "
                f"без scale — уберите scale из цепочки «{name}»"
            )
            return None
        q = self.item(name, key, f"{path}.value")
        if q is None:
            return None
        stated = [float(t.replace(",", ".")) for t in re.findall(r"\d+(?:[.,]\d+)?", claim.quote)]
        matches: bool | None = None
        if stated:
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

    def data_notes(self, slide: sc.DataNotes, path: str) -> rd.TableSlide | None:
        """«Проверка данных»: what was fixed, what is missing, where documents disagree with
        the tables, and which instructions to the model were found and ignored."""
        rows: list[list[rd.TableCell]] = []
        for note in self.notes:
            row = _note_row(note)
            if row is not None:
                rows.append(row)
        for check in self.claim_checks:
            if check.matches is False:
                rows.append(
                    [
                        rd.TableCell(text=check.source),
                        rd.TableCell(text=f"«{_shorten(check.quote, 90)}»"),
                        rd.TableCell(
                            text=f"По таблице: {check.label.split(' › ')[-1]} — {check.value}. "
                            "На слайдах — цифра из таблицы",
                            tone="negative",
                        ),
                    ]
                )
        if not rows:
            return None
        if len(rows) > MAX_DATA_NOTES_ROWS:
            self.warnings.append(f"{path}: показаны {MAX_DATA_NOTES_ROWS} из {len(rows)} замечаний")
            rows = rows[:MAX_DATA_NOTES_ROWS]
        return rd.TableSlide(
            title=self.text(slide.title, f"{path}.title") or "",
            table=rd.TableSpec(
                columns=[
                    rd.TableColumn(header="Где", align="left", width=1.3),
                    rd.TableColumn(header="Что было", align="left", width=1.6),
                    rd.TableColumn(header="Что сделали", align="left", width=1.8),
                ],
                rows=rows,
            ),
        )


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _note_row(note: Note) -> list[rd.TableCell] | None:
    """A data-quality note as «Где / Что было / Что сделали»; plain confirmations are skipped."""
    if note.message.startswith("Проверено"):
        return None
    where = note.source.split(" › ")[-1] if note.location is None else note.location
    if note.kind == "quarantine":
        quoted = note.message.partition("не использовано: ")[2] or note.message
        # Figures from a planted paragraph are not repeated on a slide, even as a quote.
        quoted = re.sub(r"\d", "•", quoted)
        return [
            rd.TableCell(text=_shorten(where, 60)),
            rd.TableCell(text=f"Указание для модели в тексте: {_shorten(quoted, 70)}"),
            rd.TableCell(text="Не выполнялось и не использовалось", tone="negative"),
        ]
    head, _, tail = note.message.partition(": ")
    was, _, done = (tail or head).partition(" — ")
    if not done:
        was, done = head, tail
    return [
        rd.TableCell(text=_shorten(where, 60)),
        rd.TableCell(text=_shorten(was.strip(), 90)),
        rd.TableCell(
            text=_shorten(done.strip(), 90),
            tone="negative" if note.severity == "warning" else None,
        ),
    ]


def _move_small_lines(
    series: list[rd.ChartSeries], path: str, warnings: list[str]
) -> list[rd.ChartSeries]:
    """A line far smaller than the bars on the same axis is flat at the bottom: give it the
    secondary axis (when that axis is free)."""
    bars = [v for s in series if s.kind == "bar" for v in s.values if v is not None]
    if not bars or any(s.axis == "secondary" for s in series):
        return series
    top = max(abs(v) for v in bars)
    out = []
    moved = False
    for s in series:
        values = [abs(v) for v in s.values if v is not None]
        if (
            not moved
            and s.kind == "line"
            and values
            and top > 0
            and max(values) < SECONDARY_AXIS_RATIO * top
        ):
            out.append(s.model_copy(update={"axis": "secondary"}))
            warnings.append(f"{path}: линия «{s.name}» перенесена на вспомогательную ось")
            moved = True
        else:
            out.append(s)
    return out


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
