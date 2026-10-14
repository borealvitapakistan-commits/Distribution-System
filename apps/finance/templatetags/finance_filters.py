from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template

register = template.Library()


def _decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@register.filter(name="num")
def num(value, places=2):
    """Comma-grouped number, e.g. 1,520,400.00 — "—" when there's none."""
    number = _decimal(value)
    if number is None:
        return "—"
    places = int(places)
    quantum = Decimal(1).scaleb(-places)
    return f"{number.quantize(quantum, rounding=ROUND_HALF_UP):,.{places}f}"


@register.filter(name="qty")
def qty(value):
    """A quantity without trailing zeros: 1,000 not 1000.0000."""
    number = _decimal(value)
    if number is None:
        return "—"
    if number == number.to_integral_value():
        return f"{number:,.0f}"
    return f"{number.normalize():,f}"


@register.filter(name="pct")
def pct(value, places=1):
    """A percentage already out of 100: 9.5%."""
    number = _decimal(value)
    if number is None:
        return "—"
    places = int(places)
    quantum = Decimal(1).scaleb(-places)
    return f"{number.quantize(quantum, rounding=ROUND_HALF_UP)}%"


@register.filter(name="share")
def share(value, places=1):
    """A fraction shown as a percentage: 0.0214 → 2.1%."""
    number = _decimal(value)
    return pct(number * 100 if number is not None else None, places)


@register.filter(name="sign_class")
def sign_class(value):
    number = _decimal(value)
    if number is None or number == 0:
        return ""
    return "fin-positive" if number > 0 else "fin-negative"
