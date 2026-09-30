"""The advance / final payment flow shared by every kind of purchase order.

Whoever pays (the Owner paying a Manufacturer, or a Distributor paying the
Owner) settles an order in up to two parts: an ADVANCE right after the
order is placed, and a FINAL payment for whatever is left once the goods
arrive. Either part can be the whole amount or nothing, which gives the
three ways an order gets paid: 100% up front, nothing up front, or a split.
"""

from decimal import Decimal

from django.db import models


class PaymentKind(models.TextChoices):
    ADVANCE = "ADVANCE", "Advance"
    FINAL = "FINAL", "Final"


class AdvanceFinalPaymentsMixin:
    """For an order model with `grand_total`, a `payments` relation whose
    rows have `kind` and `amount`, and a nullable `pays_advance` answer.

    counted_payments() decides which payments count toward what's been
    paid — every payment by default; a model can leave out, for example,
    payments the payee has rejected."""

    def counted_payments(self):
        return list(self.payments.all())

    def _paid_of_kind(self, kind=None):
        return sum(
            (
                payment.amount
                for payment in self.counted_payments()
                if kind is None or payment.kind == kind
            ),
            Decimal("0.00"),
        )

    @property
    def advance_paid(self):
        return self._paid_of_kind(PaymentKind.ADVANCE)

    @property
    def final_paid(self):
        return self._paid_of_kind(PaymentKind.FINAL)

    @property
    def paid_so_far(self):
        return self._paid_of_kind()

    @property
    def remaining_amount(self):
        return max(self.grand_total - self.paid_so_far, Decimal("0.00"))

    def _share_of_total(self, amount):
        if not self.grand_total:
            return Decimal("0")
        return (amount * Decimal("100") / self.grand_total).quantize(Decimal("0.1"))

    @property
    def advance_percentage(self):
        return self._share_of_total(self.advance_paid)

    @property
    def final_percentage(self):
        return self._share_of_total(self.final_paid)

    @property
    def payment_scenario(self):
        """Which of the three ways this order is being paid for: all up
        front, all on receipt, or split between the two."""
        if self.pays_advance is None and not self.advance_paid:
            return "Advance not answered yet"

        if not self.advance_paid:
            return "No advance — paid in full on receipt"

        if self.advance_paid >= self.grand_total:
            return "100% paid in advance"

        return (
            f"{self.advance_percentage}% advance + "
            f"{Decimal('100') - self.advance_percentage}% on receipt"
        )
