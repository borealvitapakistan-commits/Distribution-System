from decimal import Decimal, InvalidOperation

from django import template

from apps.core.money import format_amount

register = template.Library()


@register.filter(name="money")
def money(value):
    return format_amount(value)


@register.filter(name="subtract_from_100")
def subtract_from_100(value):
    try:
        return Decimal("100") - Decimal(str(value))
    except (InvalidOperation, TypeError):
        return value
