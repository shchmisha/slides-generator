"""Text in a scenario: number placeholders and the rule that the model types no figures.

"{yoy}" → "8,2 %", "{completion[Урал]}" → "96,6 %", "{deviation[Урал]:+}" → "−16 млн ₽".
Outside placeholders only harmless numbers may appear: years, "3 квартал", counts up to 12.
"""

import re

from app.facts.models import NameRef, Quantity, Series
from app.facts.store import FactError, FactStore, normalize_label

PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)(?:\[([^\]{}]+)\])?(:\+)?\}")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
MAX_FREE_NUMBER = 12


def series_item(series: Series, key: str) -> Quantity | None:
    wanted = normalize_label(key)
    return next((i.quantity for i in series.items if normalize_label(i.key) == wanted), None)


def _free_number_ok(token: str, constants: frozenset[str]) -> bool:
    if token in constants:
        return True
    if "," in token or "." in token:
        return False
    value = int(token)
    return value <= MAX_FREE_NUMBER or (len(token) == 4 and 1900 <= value <= 2099)


def render_text(
    text: str,
    store: FactStore,
    path: str,
    errors: list[str],
    constants: frozenset[str] = frozenset(),
) -> str:
    """Replace placeholders with formatted facts; report unknown names and typed numbers.

    `constants` are numbers the scenario itself introduced, such as the 1000 of
    "жалоб на 1000 заказов" (the factor of a `scale` operation): they are not data.
    """

    def replace(match: re.Match[str]) -> str:
        name, key, signed = match.group(1), match.group(2), bool(match.group(3))
        try:
            resolved = store.resolve(NameRef(name=name))
        except FactError as exc:
            errors.append(f"{path}: {exc}")
            return match.group(0)
        if isinstance(resolved, Series):
            if key is None:
                keys = ", ".join(i.key for i in resolved.items)
                errors.append(f"{path}: «{name}» — ряд, укажите элемент: {{{name}[ключ]}} ({keys})")
                return match.group(0)
            item = series_item(resolved, key)
            if item is None:
                keys = ", ".join(i.key for i in resolved.items)
                errors.append(f"{path}: в «{name}» нет «{key}». Есть: {keys}")
                return match.group(0)
            return item.formatted(signed=signed)
        if key is not None:
            errors.append(f"{path}: «{name}» — одно значение, ключ [{key}] не нужен")
        return resolved.formatted(signed=signed)

    literal = PLACEHOLDER.sub(" ", text)
    typed = [t for t in _NUMBER.findall(literal) if not _free_number_ok(t, constants)]
    if typed:
        shown = ", ".join(f"«{t}»" for t in typed)
        errors.append(
            f"{path}: числа {shown} написаны вручную. Каждую цифру из данных вставляйте "
            "через {имя}: добавьте значение в values или расчёт в derived"
        )
    if "{" in literal or "}" in literal:
        errors.append(
            f"{path}: непонятная подстановка — формат {{имя}}, {{имя[ключ]}} или {{имя:+}}"
        )
    return PLACEHOLDER.sub(replace, text)
