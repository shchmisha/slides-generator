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
_BRACED_NUMBER = re.compile(r"\{\s*[\d.,\s]+%?\s*\}")  # "{12}": a number dressed as a placeholder
# A word before a value that already says the direction makes its sign redundant: "вырос на
# 55,6 %", not "вырос на +55,6 %"; "недобор 16 млн ₽", not "недобор −16 млн ₽".
#
# Gap words name a size, whichever way the model computed it (fact − plan or plan − fact), so
# the value is shown without its sign. Change words describe a signed change: the sign is
# dropped only when it agrees with the word, and a contradiction ("вырос на" a negative value)
# is an error, so a decline never reads as growth.
_WITHIN = r"(?:\s+[^\s{}]+){0,3}?\s+на\s*$"  # "... отстал от плана на {x}"
_NEXT_TO = r"(?:\s+[^\s{}]+){0,2}?\s*[—–:-]?\s*$"  # "недобор {x}", "рост выручки — {x}"
_GAP = re.compile(
    r"(?:(?:отстал|превысил|превзош|недобра|перевыполн|недовыполн|выше плана|ниже плана"
    r"|не хватил)\w*" + _WITHIN + r")|(?:\b(?:недобор|перерасход|экономи|отставани|превышени"
    r"|дефицит|перевыполнени|недовыполнени)\w*" + _NEXT_TO + r")",
    re.IGNORECASE,
)
_RIGHT_BEFORE = r"\s*[—–:-]?\s*$"  # "рост {x}", "снижение — {x}"
# Verbs and comparatives say the direction only with "на" ("выше прошлого года на {x}"; in
# "ниже плана: {x}" the value is a level, not the change). Nouns also right before the value.
_UP_VERBS = r"вырос\w*|выросл\w*|увелич\w*|прибав\w*|подорож\w*|выше|больше|дороже"
_DOWN_VERBS = r"сниз\w*|сократ\w*|уменьш\w*|упал\w*|потерял\w*|подешев\w*|ниже|меньше|дешевле"
_UP_NOUNS = r"рост\w*|прирост\w*|увеличени\w*"
_DOWN_NOUNS = r"снижени\w*|падени\w*|сокращени\w*|уменьшени\w*"
_UP = re.compile(
    rf"\b(?:({_UP_VERBS}|{_UP_NOUNS}){_WITHIN}|({_UP_NOUNS}){_RIGHT_BEFORE})", re.IGNORECASE
)
_DOWN = re.compile(
    rf"\b(?:({_DOWN_VERBS}|{_DOWN_NOUNS}){_WITHIN}|({_DOWN_NOUNS}){_RIGHT_BEFORE})",
    re.IGNORECASE,
)


def _direction(before: str) -> tuple[str, str] | None:
    """("gap" | "up" | "down", the word) for a word right before a value, if any."""
    if match := _GAP.search(before):
        return "gap", match.group(0).split()[0]
    up, down = _UP.search(before), _DOWN.search(before)
    if up and (not down or up.start() > down.start()):
        return "up", up.group(1) or up.group(2)
    if down:
        return "down", down.group(1) or down.group(2)
    return None


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
    # "на 1000 заказов" only when the scenario computes a rate per 1000.
    literal = _RATE_PHRASE.sub(lambda m: "на" if m.group(1) in rate_bases else m.group(0), literal)
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

    def quantity(match: re.Match[str]) -> Quantity | None:
        name, key = match.group(1), match.group(2)
        try:
            resolved = store.resolve(NameRef(name=name))
        except FactError as exc:
            errors.append(f"{path}: {exc}")
            return None
        if isinstance(resolved, Series):
            if key is None:
                keys = ", ".join(i.key for i in resolved.items)
                errors.append(f"{path}: «{name}» — ряд, укажите элемент: {{{name}[ключ]}} ({keys})")
                return None
            item = series_item(resolved, key)
            if item is None:
                keys = ", ".join(i.key for i in resolved.items)
                errors.append(f"{path}: в «{name}» нет «{key}». Есть: {keys}")
            return item
        if key is not None:
            errors.append(f"{path}: «{name}» — одно значение, ключ [{key}] не нужен")
        return resolved

    typed = typed_numbers(text, rate_bases)
    if typed:
        shown = ", ".join(f"«{t}»" for t in typed)
        errors.append(
            f"{path}: числа {shown} написаны вручную. Каждую цифру из данных вставляйте "
            "через {имя}: добавьте значение в values или расчёт в derived. N показателя "
            "«на N» можно писать, только если есть операция rate с per=N"
        )
    literal = PLACEHOLDER.sub(" ", text)
    if braced := _BRACED_NUMBER.search(literal):
        errors.append(
            f"{path}: «{braced.group(0)}» — число в фигурных скобках не подстановка. Числа из "
            "документов на слайды не пиши: скажи словами («оценка из комментария завышена»), "
            "а само утверждение внеси в claims"
        )
    elif "{" in literal or "}" in literal:
        errors.append(
            f"{path}: непонятная подстановка — формат {{имя}}, {{имя[ключ]}} или {{имя:+}}"
        )
    out: list[str] = []
    pos = 0
    for match in PLACEHOLDER.finditer(text):
        out.append(text[pos : match.start()])
        pos = match.end()
        q = quantity(match)
        if q is None:
            out.append(match.group(0))
            continue
        direction = _direction("".join(out)) if q.value is not None else None
        if direction is not None and q.value is not None:
            kind, word = direction
            agrees = kind == "gap" or q.value == 0 or (q.value > 0) == (kind == "up")
            if agrees:
                out.append(q.model_copy(update={"value": abs(q.value)}).formatted())
            else:
                shown = q.formatted(signed=True)
                errors.append(
                    f"{path}: «{word} … {match.group(0)}» — значение {shown} противоречит слову "
                    f"«{word}». Возьми слово с другим направлением или посчитай разницу в другую "
                    "сторону"
                )
                out.append(shown)
        else:
            out.append(q.formatted(signed=bool(match.group(3))))
        # The unit is already part of the value: drop it if the text repeats it ("87,2 % %").
        if q.unit and q.value is not None:
            repeated = re.match(r"[ \u00a0]*" + re.escape(q.unit) + r"(?![\w%])", text[pos:])
            if repeated:
                pos += repeated.end()
    out.append(text[pos:])
    return "".join(out)
