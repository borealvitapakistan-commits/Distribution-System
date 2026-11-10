from decimal import Decimal, InvalidOperation

from django import template

from apps.core.money import format_amount

register = template.Library()


@register.filter(name="quantity")
def quantity(value):
    """Stock quantities without padded decimals, with readable thousands."""
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return value
        rendered = format(number, ",f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    except (InvalidOperation, TypeError, ValueError):
        return value


@register.filter(name="money")
def money(value):
    return format_amount(value)


@register.filter(name="subtract_from_100")
def subtract_from_100(value):
    try:
        return Decimal("100") - Decimal(str(value))
    except (InvalidOperation, TypeError):
        return value


@register.filter
def in_words(amount):
    """{{ order.grand_total|in_words }} -> "Rupees ... Only"."""
    from apps.core.amount_words import amount_in_words

    return amount_in_words(amount)
