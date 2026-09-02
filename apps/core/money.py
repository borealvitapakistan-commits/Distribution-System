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