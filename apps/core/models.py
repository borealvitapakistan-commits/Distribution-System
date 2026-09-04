import uuid
from django.db import models
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from decimal import Decimal
from apps.core.querysets import CompanyQuerySet, CompanyScopedQuerySet
from django.db.models import F, Q
from django.core.exceptions import ValidationError
import calendar
from datetime import date


class UUIDModel(models.Model):
    """Provides UUID a Primary Key"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    class Meta:
        abstract = True



class TimeStampedModel(models.Model):
    """Track Record creation and modification time"""

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True


class AuditedModel(UUIDModel, TimeStampedModel):
    """Tracks which user created and updated a record."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        abstract = True



class Company(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
    enlistment_number = models.CharField(
        max_length=100,
        blank=True,
    )
    ntn = models.CharField(max_length=50, blank=True)
    strn = models.CharField(
        max_length=50,
        blank=True,
    )
    base_currency = models.CharField(max_length=3, default="PKR")
    timezone = models.CharField(max_length=50, default="Asia/Karachi")
    fiscal_year_start_month = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    default_country = models.CharField(
        max_length=2,
        default="PK",
    )
    default_low_stock_threshold = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[MinValueValidator(Decimal("0"))]
    )

    logo = models.ImageField(
        upload_to="company/logos/",
        null=True,
        blank=True
    )

    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    active = models.BooleanField(default=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Companies"

    def __str__(self):
        return self.name


class EquityPartner(AuditedModel):
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="equity_partners",
    )
    name = models.CharField(max_length=200)
    share_percentage = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        validators=[
            MinValueValidator(Decimal("0.0001")),
            MaxValueValidator(Decimal("100.0000")),
        ],
    )

    active_from = models.DateField()
    active_to = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(active_to__isnull=True)
                    | Q(active_to__gte=F("active_from"))
                ),
                name="equity_partner_valid_dates",
            ),
            models.CheckConstraint(
                condition=(
                    Q(share_percentage__gt=0)
                    & Q(share_percentage__lte=100)
                ),
                name="equity_partner_valid_share",
            ),
        ]

    def __str__(self):
        return self.name


class FiscalPeriod(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"
        LOCKED = "LOCKED", "Locked"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="fiscal_periods"
    )

    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closed_fiscal_periods",
    )

    closed_at = models.DateTimeField(null=True, blank=True)

    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="locked_fiscal_periods",
    )

    locked_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        ordering = [
            "-year",
            "-month",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "year",
                    "month",
                ],
                name=(
                    "unique_company_fiscal_period"
                ),
            ),
            models.CheckConstraint(
                condition=(
                    Q(month__gte=1)
                    & Q(month__lte=12)
                ),
                name=(
                    "fiscal_period_valid_month"
                ),
            ),
            models.CheckConstraint(
                condition=Q(
                    start_date__lte=F(
                        "end_date"
                    )
                ),
                name=(
                    "fiscal_period_valid_dates"
                ),
            ),
        ]

    def clean(self):
        super().clean()

        if (
            not self.year
            or not self.month
            or not 1 <= self.month <= 12
        ):
            return

        expected_start = date(
            self.year,
            self.month,
            1,
        )

        expected_end = date(
            self.year,
            self.month,
            calendar.monthrange(
                self.year,
                self.month,
            )[1],
        )

        if (
            self.start_date != expected_start
            or self.end_date != expected_end
        ):
            raise ValidationError(
                "Fiscal-period dates must match "
                "the selected year and month."
            )

    def __str__(self):
        return (
            f"{self.company.name} - "
            f"{self.year}-{self.month:02d}"
        )


class DocumentSequence(UUIDModel, TimeStampedModel):
    class DocumentType(models.TextChoices):
        PURCHASE_ORDER = "PO", "Purchase Order"
        GOODS_RECEIPT = "GRN", "Goods Receipt"
        STOCK_MOVEMENT = "MOV", "Stock Movement"
        TRANSFER = "TRF", "Transfer"
        STOCK_REQUEST = "REQ", "Stock Request"
        ORDER = "ORD", "Order"
        DISPATCH = "DSP", "Dispatch"
        INVOICE = "INV", "Invoice"
        RECEIPT = "RCPT", "Receipt"
        CREDIT_NOTE = "CRN", "Credit Note"
        RETURN = "RET", "Return"
        EXPENSE = "EXP", "Expense"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="document_sequences",
    )

    document_type = models.CharField(max_length=10, choices=DocumentType.choices)
    prefix = models.CharField(max_length=10)
    year = models.PositiveIntegerField()

    next_number = models.PositiveBigIntegerField(default=1)

    padding = models.PositiveSmallIntegerField(
        default=6,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(12),
        ],
    )

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-year", "document_type"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "document_type",
                    "year",
                ],
                name="unique_company_document_sequence",
            ),
            models.CheckConstraint(
                condition=Q(next_number__gte=1),
                name="document_sequence_positive_number",
            ),
        ]

    def __str__(self):
        return (
            f"{self.company.name} - "
            f"{self.document_type} - {self.year}"
        )











