"""Text in a scenario: number placeholders and the rule that the model types no figures.

"{yoy}" → "8,2 %", "{completion[Урал]}" → "96,6 %", "{deviation[Урал]:+}" → "−16 млн ₽".
Outside placeholders only harmless numbers may appear: years, "3 квартал", counts up to 12,
"100 %" and the N of a rate phrase "на 1000 заказов".
"""

import re

from app.facts.models import NameRef, Quantity, Series
from app.facts.store import FactError, FactStore, normalize_label

PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)(?:\[([^\]{}]+)\])?(:\+)?\}")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# "на 1000 заказов", "на 100 клиентов": the N of a rate, written before a word.
_RATE_PHRASE = re.compile(r"\bна\s(10|100|1000|10000|100000)(?=\s[\w«])", re.IGNORECASE)
_HUNDRED_PERCENT = re.compile(r"(?<![\d,.])100\s?%")
# Figures spelled out in words would slip past a digit check.
_NUMBER_WORDS = re.compile(
    r"\b(тысяч\w*|миллион\w*|миллиард\w*|сотн\w*|десятк\w*|"
    r"(двадцат|тридцат|сорок|пятьдесят|шестьдесят|семьдесят|восемьдесят|девяност|"
    r"двест|трист|четырест|пятьсот|шестьсот|семьсот|восемьсот|девятьсот)\w*)\b",
    re.IGNORECASE,
)
MAX_FREE_NUMBER = 12


def _words(label: str) -> list[str]:
    return normalize_label(label).split()


def series_item(series: Series, key: str) -> Quantity | None:
    """An item by its key; "Сентябрь факт" also finds "Сентябрь" (and the other way round)
    when exactly one item matches by its leading words."""
    wanted = normalize_label(key)
    exact = [i for i in series.items if normalize_label(i.key) == wanted]
    if exact:
        return exact[0].quantity
    want = _words(key)
    loose = [
        i
        for i in series.items
        if (have := _words(i.key))
        and want
        and (want[: len(have)] == have or have[: len(want)] == want)
    ]
    return loose[0].quantity if len(loose) == 1 else None


def _free_number_ok(token: str) -> bool:
    if "," in token or "." in token:
        return False
    value = int(token)
    return value <= MAX_FREE_NUMBER or (len(token) == 4 and 1900 <= value <= 2099)


def typed_numbers(text: str, rate_bases: frozenset[str] = frozenset()) -> list[str]:
    """Figures written by hand outside placeholders (empty when the text is clean).

    `rate_bases` are the N of the scenario's own rate operations ("жалоб / 1000"): powers of
    ten fixed by the schema, so they can label a rate but cannot fake a figure.
    """
    literal = PLACEHOLDER.sub(" ", text)
    literal = _RATE_PHRASE.sub("на", literal)
    literal = _HUNDRED_PERCENT.sub(" ", literal)
    found = [t for t in _NUMBER.findall(literal) if not _free_number_ok(t) and t not in rate_bases]
    found += [m.group(0) for m in _NUMBER_WORDS.finditer(literal)]
    return found


def render_text(
    text: str,
    store: FactStore,
    path: str,
    errors: list[str],
    rate_bases: frozenset[str] = frozenset(),
) -> str:
    """Replace placeholders with formatted facts; report unknown names and typed numbers."""

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

    typed = typed_numbers(text, rate_bases)
    if typed:
        shown = ", ".join(f"«{t}»" for t in typed)
        errors.append(
            f"{path}: числа {shown} написаны вручную. Каждую цифру из данных вставляйте "
            "через {имя}: добавьте значение в values или расчёт в derived. N показателя "
            "«на N» можно писать, только если есть операция rate с per=N"
        )
    literal = PLACEHOLDER.sub(" ", text)
    if "{" in literal or "}" in literal:
        errors.append(
            f"{path}: непонятная подстановка — формат {{имя}}, {{имя[ключ]}} или {{имя:+}}"
        )
    return PLACEHOLDER.sub(replace, text)
