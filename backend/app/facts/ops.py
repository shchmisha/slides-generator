"""Calculations the agent may request when a figure is not in the source.

Every operation works on single values or on series (element by element, matched by label,
so "this year by region" can be combined with "last year by region" from another table).
A missing input or a division by zero gives a missing result plus a warning — never a guess.
Each result keeps its formula with the actual numbers, which is what the user reviews.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.facts.format import MISSING_TEXT, format_number
from app.facts.models import (
    Derived,
    DifferenceOp,
    MeanOp,
    Operation,
    PercentChangeOp,
    PercentOfOp,
    Quantity,
    RateOp,
    RatioOp,
    ScaleOp,
    Series,
    SeriesItem,
    ShareOp,
    SumOp,
)
from app.facts.store import FactError, FactStore, normalize_label

Resolved = Quantity | Series


@dataclass(frozen=True)
class _Kernel:
    compute: Callable[[float, float], float]  # may raise ZeroDivisionError
    formula: Callable[[str, str, str], str]  # (x, y, result) → "x \u2212 y = result"
    unit: Callable[[Quantity, Quantity], str | None]
    decimals: Callable[[Quantity, Quantity], int]


def _same_unit(x: Quantity, y: Quantity) -> str | None:
    if x.unit == y.unit == "%":
        return "п.п."  # a difference of percentages is in percentage points
    return x.unit if x.unit == y.unit else None


def _max_decimals(x: Quantity, y: Quantity) -> int:
    return max(x.decimals, y.decimals)


def _percent(_: Quantity, __: Quantity) -> str:
    return "%"


def _one(_: Quantity, __: Quantity) -> int:
    return 1


DIFFERENCE = _Kernel(
    compute=lambda x, y: x - y,
    formula=lambda x, y, r: f"{x} \u2212 {y} = {r}",
    unit=_same_unit,
    decimals=_max_decimals,
)
PERCENT_OF = _Kernel(
    compute=lambda x, y: x / y * 100,
    formula=lambda x, y, r: f"{x} / {y} × 100 = {r}",
    unit=_percent,
    decimals=_one,
)
PERCENT_CHANGE = _Kernel(
    compute=lambda x, y: (x - y) / y * 100,
    formula=lambda x, y, r: f"({x} \u2212 {y}) / {y} × 100 = {r}",
    unit=_percent,
    decimals=_one,
)


def _ratio(unit: str | None) -> _Kernel:
    return _Kernel(
        compute=lambda x, y: x / y,
        formula=lambda x, y, r: f"{x} / {y} = {r}",
        unit=lambda _x, _y: unit,
        decimals=lambda _x, _y: 2,
    )


def _rate(per: int, unit: str | None) -> _Kernel:
    return _Kernel(
        compute=lambda x, y: x / y * per,
        formula=lambda x, y, r: f"{x} / {y} × {format_number(per)} = {r}",
        unit=lambda _x, _y: unit,
        decimals=_one,
    )


def _scale(unit: str | None) -> _Kernel:
    return _Kernel(
        compute=lambda x, y: x * y,
        formula=lambda x, y, r: f"{x} × {y} = {r}",
        unit=lambda x, _y: unit if unit is not None else x.unit,
        decimals=_one,
    )


def _shown(q: Quantity) -> str:
    return format_number(q.value, q.decimals) if q.value is not None else MISSING_TEXT


class _Evaluation:
    def __init__(self, op: Operation) -> None:
        self.op = op
        self.warnings: list[str] = []

    def missing(self, label: str, unit: str | None, decimals: int, reason: str) -> Quantity:
        self.warnings.append(f"«{label}» не посчитано: {reason}")
        return Quantity(value=None, unit=unit, decimals=decimals, label=label, origin=reason)

    def pair(self, x: Quantity, y: Quantity, kernel: _Kernel, label: str) -> Quantity:
        unit = kernel.unit(x, y)
        decimals = self.op.decimals if self.op.decimals is not None else kernel.decimals(x, y)
        if x.value is None or y.value is None:
            gap = x if x.value is None else y
            return self.missing(label, unit, decimals, f"нет данных — {gap.label}")
        try:
            value = kernel.compute(x.value, y.value)
        except ZeroDivisionError:
            return self.missing(label, unit, decimals, f"деление на ноль — {y.label}")
        result = Quantity(value=value, unit=unit, decimals=decimals, label=label, origin="")
        formula = kernel.formula(_shown(x), _shown(y), result.formatted())
        return result.model_copy(update={"origin": formula})

    def apply(self, a: Resolved, b: Resolved, kernel: _Kernel) -> Resolved:
        label = self.op.label
        if isinstance(a, Quantity) and isinstance(b, Quantity):
            return self.pair(a, b, kernel, label)
        items: list[SeriesItem] = []
        if isinstance(a, Series) and isinstance(b, Quantity):
            for item in a.items:
                q = self.pair(item.quantity, b, kernel, f"{label} › {item.key}")
                items.append(SeriesItem(key=item.key, quantity=q))
        elif isinstance(a, Quantity) and isinstance(b, Series):
            for item in b.items:
                q = self.pair(a, item.quantity, kernel, f"{label} › {item.key}")
                items.append(SeriesItem(key=item.key, quantity=q))
        elif isinstance(a, Series) and isinstance(b, Series):
            for key, x, y in self.align(a, b):
                item_label = f"{label} › {key}"
                if y is None:
                    q = self.missing(item_label, None, 0, f"в «{b.label}» нет «{key}»")
                else:
                    q = self.pair(x, y, kernel, item_label)
                items.append(SeriesItem(key=key, quantity=q))
        return _series(label, items)

    def align(self, a: Series, b: Series) -> list[tuple[str, Quantity, Quantity | None]]:
        """Pair the items of two series: by label, or by position when the labels differ
        but the lengths match ("Июль факт" in one table, "Июль" in another)."""
        others = {normalize_label(item.key): item.quantity for item in b.items}
        if all(normalize_label(item.key) in others for item in a.items):
            return [(i.key, i.quantity, others[normalize_label(i.key)]) for i in a.items]
        if len(a.items) == len(b.items):
            pairs = list(zip(a.items, b.items, strict=True))
            self.warnings.append(
                "Ряды сопоставлены по порядку: "
                + ", ".join(f"«{x.key}» ↔ «{y.key}»" for x, y in pairs)
            )
            return [(_common_key(x.key, y.key), x.quantity, y.quantity) for x, y in pairs]
        return [(i.key, i.quantity, others.get(normalize_label(i.key))) for i in a.items]

    def total(self, quantities: list[Quantity], label: str) -> Quantity:
        units = {q.unit for q in quantities}
        unit = units.pop() if len(units) == 1 else None
        decimals = (
            self.op.decimals
            if self.op.decimals is not None
            else max((q.decimals for q in quantities), default=0)
        )
        for q in quantities:
            if q.value is None:
                return self.missing(label, unit, decimals, f"нет данных — {q.label}")
        value = sum(q.value for q in quantities if q.value is not None)
        result = Quantity(value=value, unit=unit, decimals=decimals, label=label, origin="")
        formula = " + ".join(_shown(q) for q in quantities) + f" = {result.formatted()}"
        return result.model_copy(update={"origin": formula})


def _common_key(a: str, b: str) -> str:
    """Shared leading words of two labels: "Июль факт" + "Июль" → "Июль"."""
    common = []
    for x, y in zip(a.split(), b.split(), strict=False):
        if normalize_label(x) != normalize_label(y):
            break
        common.append(x)
    return " ".join(common) or a


def _series(label: str, items: list[SeriesItem]) -> Series:
    units = {item.quantity.unit for item in items}
    return Series(label=label, unit=units.pop() if len(units) == 1 else None, items=items)


def _sum(op: SumOp, store: FactStore, ev: _Evaluation) -> Resolved:
    resolved = [store.resolve(ref) for ref in op.of]
    if len(resolved) == 1 and isinstance(resolved[0], Series):
        return ev.total([item.quantity for item in resolved[0].items], op.label)
    if all(isinstance(r, Quantity) for r in resolved):
        return ev.total([r for r in resolved if isinstance(r, Quantity)], op.label)
    if all(isinstance(r, Series) for r in resolved):
        first, *rest = [r for r in resolved if isinstance(r, Series)]
        keys = [item.key for item in first.items]
        columns: list[list[Quantity | None]] = [[item.quantity for item in first.items]]
        for other in rest:
            aligned = ev.align(first, other)
            keys = [
                key if len(key) < len(old) else old
                for (key, _, _), old in zip(aligned, keys, strict=True)
            ]
            columns.append([y for _, _, y in aligned])
        items = []
        for i, key in enumerate(keys):
            parts = [column[i] for column in columns]
            item_label = f"{op.label} › {key}"
            present = [q for q in parts if q is not None]
            if len(present) != len(parts):
                q = ev.missing(item_label, None, 0, f"«{key}» есть не во всех рядах")
            else:
                q = ev.total(present, item_label)
            items.append(SeriesItem(key=key, quantity=q))
        return _series(op.label, items)
    raise FactError("sum: нельзя смешивать отдельные значения и ряды")


def _mean(op: MeanOp, of: Resolved, ev: _Evaluation) -> Quantity:
    if not isinstance(of, Series):
        raise FactError("mean считается только для ряда значений")
    present = [i.quantity for i in of.items if i.quantity.value is not None]
    skipped = [i.key for i in of.items if i.quantity.value is None]
    if skipped:
        ev.warnings.append(f"«{op.label}»: без «{', '.join(skipped)}» — нет данных")
    decimals = (
        op.decimals
        if op.decimals is not None
        else max((q.decimals for q in present), default=0) + 1
    )
    if not present:
        return ev.missing(op.label, of.unit, decimals, f"нет данных — {of.label}")
    value = sum(q.value for q in present if q.value is not None) / len(present)
    result = Quantity(value=value, unit=of.unit, decimals=decimals, label=op.label, origin="")
    shown = " + ".join(_shown(q) for q in present)
    formula = f"({shown}) / {len(present)} = {result.formatted()}"
    return result.model_copy(update={"origin": formula})


def _share_of_total(op: ShareOp, part: Resolved, ev: _Evaluation) -> Resolved:
    if not isinstance(part, Series):
        raise FactError("share без whole считается только для ряда значений")
    total = ev.total([item.quantity for item in part.items], f"{op.label}: сумма")
    items = [
        SeriesItem(
            key=item.key,
            quantity=ev.pair(item.quantity, total, PERCENT_OF, f"{op.label} › {item.key}"),
        )
        for item in part.items
    ]
    return _series(op.label, items)


def evaluate(op: Operation, store: FactStore) -> Derived:
    """Run one operation and register its result in the store under `op.id`."""
    ev = _Evaluation(op)
    result: Resolved
    match op:
        case SumOp():
            result = _sum(op, store, ev)
        case DifferenceOp():
            result = ev.apply(store.resolve(op.a), store.resolve(op.b), DIFFERENCE)
        case RatioOp():
            result = ev.apply(store.resolve(op.a), store.resolve(op.b), _ratio(op.unit))
        case PercentOfOp():
            result = ev.apply(store.resolve(op.part), store.resolve(op.whole), PERCENT_OF)
        case PercentChangeOp():
            result = ev.apply(store.resolve(op.new), store.resolve(op.old), PERCENT_CHANGE)
        case ShareOp():
            part = store.resolve(op.part)
            if op.whole is None:
                result = _share_of_total(op, part, ev)
            else:
                result = ev.apply(part, store.resolve(op.whole), PERCENT_OF)
        case ScaleOp():
            factor = Quantity(
                value=op.factor,
                unit=None,
                decimals=0 if float(op.factor).is_integer() else 2,
                label=f"× {op.factor:g}",
                origin="константа",
            )
            result = ev.apply(store.resolve(op.a), factor, _scale(op.unit))
        case RateOp():
            result = ev.apply(store.resolve(op.a), store.resolve(op.b), _rate(op.per, op.unit))
        case MeanOp():
            result = _mean(op, store.resolve(op.of), ev)
    derived = Derived(id=op.id, label=op.label, op=op.op, result=result, warnings=ev.warnings)
    store.add_derived(derived)
    return derived
