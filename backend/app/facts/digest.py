"""Data summary for the prompt: what the tables say, computed by code.

The model writes the words around the numbers, and gets them wrong when it has to do the
arithmetic in its head: "profit falls" when it went 124 → 105 → 133, "everyone grew except
Урал" when Урал grew too, "complaints are rising" from a single-period table. This summary
states those facts so the model only has to read them. Its numbers are for understanding;
slide text still gets numbers only through placeholders.

Shapes are detected generically: period columns (months, quarters, years), plan/fact column
pairs, total rows, a prior-year table with the same rows, and revenue vs costs tables over the
same periods. The keywords are ordinary business vocabulary, not tied to one file.
"""

from __future__ import annotations

import re
from itertools import pairwise

from app.facts.format import format_number
from app.facts.models import Row, Table
from app.facts.store import normalize_label

_PERIOD = re.compile(
    r"^(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр"
    r"|q[1-4]|[1-4]\s*кв|20\d\d)",
    re.IGNORECASE,
)
_LOWER_IS_BETTER = re.compile(r"жалоб|затрат|расход|опоздан|брак|отказ|возврат|долг", re.I)
_PRIOR_YEAR = re.compile(r"прошл\w* год|предыдущ\w* год", re.I)
_REVENUE = re.compile(r"выручк|продаж|доход", re.I)
_COSTS = re.compile(r"затрат|расход", re.I)
MAX_RANKED = 12


def _period(label: str) -> str | None:
    words = normalize_label(label).split()
    return words[0] if words and _PERIOD.match(words[0]) else None


def _num(value: float, decimals: int = 0) -> str:
    return format_number(value, decimals)


def _steps(values: list[float]) -> str:
    return " ".join("↑" if b > a else "↓" if b < a else "=" for a, b in pairwise(values))


def _change(first: float, last: float) -> str:
    return format_number((last - first) / first * 100, 1, signed=True) + " %" if first else "—"


class _Shape:
    """Where periods, plan and fact are in one table."""

    def __init__(self, table: Table) -> None:
        self.table = table
        self.numeric = [(i, c) for i, c in enumerate(table.columns) if c.numeric]
        self.periodic = [(i, c) for i, c in self.numeric if _period(c.label)]
        self.body = [r for r in table.rows if not r.is_total]
        self.totals = [r for r in table.rows if r.is_total]
        plan = {_period(c.label): i for i, c in self.periodic if "план" in c.label.casefold()}
        fact = {_period(c.label): i for i, c in self.periodic if "факт" in c.label.casefold()}
        self.pairs = [(p, plan[p], fact[p]) for p in fact if p in plan]
        # The series over time: fact columns when there is a plan/fact split, else all periods.
        self.series_cols = [
            (i, c) for i, c in self.periodic if not self.pairs or "факт" in c.label.casefold()
        ]

    def series(self, row: Row) -> list[tuple[str, float]]:
        points = []
        for i, column in self.series_cols:
            value = row.cells[i].value
            period = _period(column.label)
            if value is not None and period:
                points.append((period, value))
        return points


def _period_lines(shape: _Shape) -> list[str]:
    t = shape.table
    lines = []
    if not shape.periodic:
        lines.append(
            f"[{t.id}] «{t.title}» — один период: сравнивай строки между собой; слов о динамике "
            "(растёт, снижается, ухудшается, рост, просадка) по этим показателям не пиши — "
            "прошлых значений нет"
        )
        return lines
    rows = shape.totals + (shape.body if len(shape.body) <= 8 else [])
    for row in rows:
        points = shape.series(row)
        if len(points) < 2:
            continue
        values = [v for _, v in points]
        decimals = max(row.cells[i].decimals for i, _ in shape.series_cols)
        lines.append(
            f"[{t.id}] {row.label}{', факт' if shape.pairs else ''} по периодам: "
            + " → ".join(f"{p} {_num(v, decimals)}" for p, v in points)
            + f" ({_steps(values)}); {points[0][0]}→{points[-1][0]}: "
            + _change(values[0], values[-1])
        )
    if shape.pairs:
        below_lines = []
        for row in shape.body:
            below = [
                (p, row.cells[f].value - row.cells[pl].value)  # type: ignore[operator]
                for p, pl, f in shape.pairs
                if row.cells[f].value is not None
                and row.cells[pl].value is not None
                and row.cells[f].value < row.cells[pl].value  # type: ignore[operator]
            ]
            if below:
                below_lines.append(
                    f"{row.label} — " + ", ".join(f"{p} ({_num(d)})" for p, d in below)
                )
        never = [
            r.label
            for r in shape.body
            if not any(line.startswith(f"{r.label} — ") for line in below_lines)
        ]
        if below_lines:
            lines.append(
                f"[{t.id}] ниже плана по периодам: "
                + "; ".join(below_lines)
                + (f"; ни в одном периоде не ниже плана: {', '.join(never)}" if never else "")
            )
        else:
            lines.append(f"[{t.id}] ни одна строка не была ниже плана ни в одном периоде")
    return lines


def _ranking_lines(shape: _Shape) -> list[str]:
    t = shape.table
    lines: list[str] = []
    if len(shape.body) < 3:
        return lines
    for i, column in shape.numeric:
        if _period(column.label) or "план" in column.label.casefold():
            continue
        present = [(r.label, r.cells[i].value, r.cells[i].decimals) for r in shape.body]
        known = [(k, v, d) for k, v, d in present if v is not None]
        missing = [k for k, v, _ in present if v is None]
        if len(known) < 3:
            continue
        ranked = sorted(known, key=lambda kv: kv[1], reverse=True)[:MAX_RANKED]
        hint = " (меньше — лучше)" if _LOWER_IS_BETTER.search(column.label) else ""
        lines.append(
            f"[{t.id}] «{column.label}» по убыванию{hint}: "
            + "; ".join(f"{k} {_num(v, d)}" for k, v, d in ranked)
            + (f"; нет данных: {', '.join(missing)} — не ставь их в рейтинг" if missing else "")
        )
    counts = [(i, c) for i, c in shape.numeric if (t.unit_of(c) or "").startswith("шт")]
    if len(counts) >= 2 and not shape.periodic:
        base_i, base = max(
            counts, key=lambda ic: sum(r.cells[ic[0]].value or 0 for r in shape.body)
        )
        for i, column in counts:
            if i == base_i:
                continue
            rates: list[tuple[str, float]] = []
            for r in shape.body:
                part, whole = r.cells[i].value, r.cells[base_i].value
                if part is not None and whole:
                    rates.append((r.label, part / whole * 1000))
            by_rate = sorted(rates, key=lambda kv: kv[1], reverse=True)
            lines.append(
                f"[{t.id}] «{column.label}» на 1000 «{base.label}» по убыванию: "
                + "; ".join(f"{k} {_num(v, 1)}" for k, v in by_rate)
                + f". Абсолютные «{column.label}» у строк разного размера не сравнивай; "
                f"считай rate(a=«{column.label}», b=«{base.label}», per=1000)"
            )
    return lines


def _total_column(table: Table) -> int | None:
    labels = [normalize_label(c.label) for c in table.columns]
    for wanted in ("итого факт", "итого", "всего"):
        if wanted in labels:
            return labels.index(wanted)
    return None


def _prior_year_lines(tables: list[Table]) -> list[str]:
    lines = []
    for prior in tables:
        if not _PRIOR_YEAR.search(prior.title):
            continue
        p_total = _total_column(prior)
        prior_rows = {normalize_label(r.label): r for r in prior.rows if not r.is_total}
        for current in tables:
            if current is prior or _PRIOR_YEAR.search(current.title):
                continue
            c_total = _total_column(current)
            rows = [r for r in current.rows if not r.is_total]
            if (
                p_total is None
                or c_total is None
                or {normalize_label(r.label) for r in rows} != set(prior_rows)
            ):
                continue
            growth = []
            for row in rows:
                now, before = (
                    row.cells[c_total].value,
                    prior_rows[normalize_label(row.label)].cells[p_total].value,
                )
                if now is not None and before:
                    growth.append((row.label, (now - before) / before * 100))
            ordered = sorted(growth, key=lambda kv: kv[1], reverse=True)
            down = [k for k, g in growth if g < 0]
            lines.append(
                f"[{current.id} к {prior.id}] рост к прошлому году по строкам: "
                + "; ".join(f"{k} {format_number(g, 1, signed=True)} %" for k, g in ordered)
                + (
                    f". Ниже прошлого года: {', '.join(down)}"
                    if down
                    else ". Ниже прошлого года — никто"
                )
            )
    return lines


def _profit_lines(shapes: list[_Shape]) -> list[str]:
    lines = []
    for revenue in shapes:
        for costs in shapes:
            r_t, c_t = revenue.table, costs.table
            if revenue is costs or not (_REVENUE.search(r_t.title) and _COSTS.search(c_t.title)):
                continue
            if _PRIOR_YEAR.search(r_t.title) or _PRIOR_YEAR.search(c_t.title):
                continue
            if not revenue.totals or not costs.totals or r_t.unit != c_t.unit:
                continue
            income, spend = (
                dict(revenue.series(revenue.totals[0])),
                dict(costs.series(costs.totals[0])),
            )
            periods = [p for p in income if p in spend]
            if len(periods) < 2:
                continue
            profit = [income[p] - spend[p] for p in periods]
            margin = [income[p] and (income[p] - spend[p]) / income[p] * 100 for p in periods]
            first, last = periods[0], periods[-1]
            lines.append(
                f"[{r_t.id} − {c_t.id}] прибыль (выручка − затраты): "
                + " → ".join(f"{p} {_num(v)}" for p, v in zip(periods, profit, strict=True))
                + f" ({_steps(profit)}); маржа: "
                + " → ".join(f"{_num(m, 1)} %" for m in margin)
                + f" ({_steps(margin)}). За {first}→{last}: "
                f"выручка {_change(income[first], income[last])}, "
                f"затраты {_change(spend[first], spend[last])}. Посчитай: "
                "difference(выручка, затраты) по периодам, затем percent_of(прибыль, выручка)"
            )
    return lines


def is_single_period(table: Table) -> bool:
    """A snapshot table: no month/quarter/year columns, so no change over time can be shown."""
    return not _Shape(table).periodic


def build_digest(tables: list[Table]) -> list[str]:
    shapes = [_Shape(t) for t in tables]
    lines: list[str] = []
    for shape in shapes:
        lines += _period_lines(shape)
        lines += _ranking_lines(shape)
    lines += _prior_year_lines(tables)
    lines += _profit_lines(shapes)
    return lines
