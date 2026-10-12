"""Russian number formatting shared by the data report, the scenario and the slides."""

NBSP = "\u00a0"
MINUS = "\u2212"
MISSING_TEXT = "нет данных"


def format_number(value: float, decimals: int = 0, *, signed: bool = False) -> str:
    """2877 → "2 877", 8.24 → "8,2" (decimals=1), -16 → "\u221216"; spaces are no-break."""
    rounded = round(abs(value), decimals)
    digits = f"{rounded:,.{decimals}f}".replace(",", NBSP).replace(".", ",")
    if rounded == 0:
        return digits
    if value < 0:
        return MINUS + digits
    return ("+" if signed else "") + digits


def format_quantity(
    value: float | None, unit: str | None, decimals: int = 0, *, signed: bool = False
) -> str:
    if value is None:
        return MISSING_TEXT
    number = format_number(value, decimals, signed=signed)
    return f"{number}{NBSP}{unit}" if unit else number
