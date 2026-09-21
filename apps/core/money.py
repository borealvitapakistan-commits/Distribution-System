from decimal import Decimal, ROUND_HALF_UP


PAISA_PER_RUPEE = Decimal("100")
TWO_DECIMAL_PLACES = Decimal("0.01")


def rupees_to_paisa(amount):
    """
    Convert PKR rupees into integer paisa.

    Example:
    1250.50 rupees becomes 125050 paisa.
    """

    decimal_amount = Decimal(str(amount))
    paisa = (decimal_amount * PAISA_PER_RUPEE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    return int(paisa)


def paisa_to_rupees(amount_paisa):
    """
    Convert integer paisa into PKR rupees.

    Example:
    125050 paisa becomes 1250.50 rupees.
    """

    rupees = (Decimal(amount_paisa) / PAISA_PER_RUPEE)

    return rupees.quantize(TWO_DECIMAL_PLACES)


def short_suffix(amount):
    """South-Asian shorthand for a rupee amount: Crore (1,00,00,000+),
    Lakh (1,00,000+), or k (1,000+). Empty string below 1,000."""

    try:
        value = abs(Decimal(str(amount)))
    except (TypeError, ValueError):
        return ""

    if value >= Decimal("10000000"):
        number = (value / Decimal("10000000")).quantize(Decimal("0.01"))
        return f"{number} CR"

    if value >= Decimal("100000"):
        number = (value / Decimal("100000")).quantize(Decimal("0.1"))
        return f"{number} Lakh"

    if value >= Decimal("1000"):
        number = (value / Decimal("1000")).quantize(Decimal("0.1"))
        return f"{number}k"

    return ""


def format_amount(amount):
    """Comma-grouped amount with a South-Asian shorthand in brackets,
    e.g. "1,760,000.00 (17.6 Lakh)". Falls back to the raw value if it
    isn't a number."""

    try:
        value = Decimal(str(amount))
    except (TypeError, ValueError):
        return amount

    grouped = f"{value:,.2f}"
    suffix = short_suffix(value)

    return f"{grouped} ({suffix})" if suffix else grouped