from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import AuditedModel


PERCENTAGE_VALIDATORS = [
    MinValueValidator(Decimal("0")),
    MaxValueValidator(Decimal("100")),
]


class AgreementQuerySet(models.QuerySet):
    def for_user(self, user):
        if not (user and user.is_authenticated and user.is_active):
            return self.none()

        if user.is_owner:
            return self.all()

        profile = getattr(user, "distributor_profile", None)

        if profile is not None:
            return self.filter(distributor_profile=profile)

        return self.none()

    def awaiting_signature(self):
        return self.filter(status=Agreement.Status.SENT)

    def in_force_on(self, day):
        """Signed agreements whose start..end window covers `day`."""
        return self.filter(
            status=Agreement.Status.ACCEPTED,
            start_date__lte=day,
            end_date__gte=day,
        )


class Agreement(AuditedModel):
    """The Owner's pricing deal with one Distributor: a default discount
    off each product's list price (optionally a different one for
    specific products), valid between two dates. The Owner signs it by
    sending it; it only prices orders once the Distributor signs too."""

    class Status(models.TextChoices):
        SENT = "SENT", "Awaiting Distributor's signature"
        ACCEPTED = "ACCEPTED", "Signed"
        DECLINED = "DECLINED", "Declined by Distributor"
        CANCELLED = "CANCELLED", "Cancelled by Owner"

    agreement_number = models.CharField(max_length=20, unique=True, editable=False)

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="agreements",
    )

    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENTAGE_VALIDATORS,
        help_text="Default discount off the list price, e.g. 18 = 1,000 becomes 820.",
    )

    start_date = models.DateField()
    end_date = models.DateField()
    terms = models.TextField(blank=True)

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.SENT,
    )

    owner_signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    owner_signed_at = models.DateTimeField(default=timezone.now)

    distributor_signature = models.CharField(
        max_length=200,
        blank=True,
        help_text="The full name the Distributor typed to sign.",
    )
    distributor_signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )
    distributor_signed_at = models.DateTimeField(null=True, blank=True)

    response_note = models.CharField(
        max_length=255,
        blank=True,
        help_text="Why it was declined or cancelled.",
    )

    objects = AgreementQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date", "-created_at"]

    def clean(self):
        super().clean()

        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The end date can't be before the start date."})

    def covers(self, day):
        return self.start_date <= day <= self.end_date

    @property
    def is_expired(self):
        return self.end_date < timezone.localdate()

    @property
    def state_label(self):
        """What a person cares about at a glance — a signed agreement can
        still be upcoming or already expired."""
        if self.status != self.Status.ACCEPTED:
            return self.get_status_display()

        today = timezone.localdate()

        if today < self.start_date:
            return "Signed — starts later"

        if today > self.end_date:
            return "Expired"

        return "Active"

    @property
    def state_badge(self):
        return {
            "Active": "badge-open",
            "Signed — starts later": "badge-neutral",
            self.Status.SENT.label: "badge-pending",
        }.get(self.state_label, "badge-closed")

    @property
    def example_price(self):
        return discounted_price(Decimal("1000"), self.discount_percentage)

    def discount_for(self, product):
        """This agreement's discount for one product — its own override
        if it has one, otherwise the default."""
        for rate in self.product_rates.all():
            if rate.product_id == product.pk:
                return rate.discount_percentage

        return self.discount_percentage

    def __str__(self):
        return f"{self.agreement_number} - {self.distributor_profile.name}"


class AgreementProductRate(AuditedModel):
    """A product priced at a different discount than the agreement's
    default — e.g. 18% overall but Ashwagandha at 15%."""

    agreement = models.ForeignKey(
        Agreement,
        on_delete=models.CASCADE,
        related_name="product_rates",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="agreement_rates",
    )

    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENTAGE_VALIDATORS,
    )

    class Meta:
        ordering = ["product__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["agreement", "product"],
                name="unique_product_per_agreement",
            ),
        ]

    @property
    def price_now(self):
        return discounted_price(self.product.base_retail_price, self.discount_percentage)

    def __str__(self):
        return f"{self.agreement.agreement_number} - {self.product} - {self.discount_percentage}%"


def discounted_price(list_price, discount_percentage):
    """1,000 at 18% → 820.00."""
    factor = (Decimal("100") - Decimal(discount_percentage)) / Decimal("100")
    return (Decimal(list_price) * factor).quantize(Decimal("0.01"))
