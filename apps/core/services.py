import calendar
import time

from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    Company,
    DocumentSequence,
    FiscalPeriod,
)


DOCUMENT_PREFIXES = {
    DocumentSequence.DocumentType.PURCHASE_ORDER: "PO",
    DocumentSequence.DocumentType.GOODS_RECEIPT: "GRN",
    DocumentSequence.DocumentType.STOCK_MOVEMENT: "MOV",
    DocumentSequence.DocumentType.TRANSFER: "TRF",
    DocumentSequence.DocumentType.STOCK_REQUEST: "REQ",
    DocumentSequence.DocumentType.ORDER: "ORD",
    DocumentSequence.DocumentType.DISPATCH: "DSP",
    DocumentSequence.DocumentType.INVOICE: "INV",
    DocumentSequence.DocumentType.RECEIPT: "RCPT",
    DocumentSequence.DocumentType.CREDIT_NOTE: "CRN",
    DocumentSequence.DocumentType.RETURN: "RET",
    DocumentSequence.DocumentType.EXPENSE: "EXP",
}


def require_owner(user, company):
    if not user or not getattr(
        user,
        "is_authenticated",
        False,
    ):
        raise PermissionDenied(
            "Authentication is required."
        )

    if not user.is_active or not user.is_owner:
        raise PermissionDenied(
            "Only an active Owner can perform this action."
        )

    if (
        not user.company_id
        or user.company_id != company.pk
        or not company.active
    ):
        raise PermissionDenied(
            "Cross-company access is prohibited."
        )


def company_local_date(company):
    try:
        company_timezone = ZoneInfo(
            company.timezone
        )
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(
            "The company timezone is invalid."
        ) from exc

    return (
        timezone.now()
        .astimezone(company_timezone)
        .date()
    )


def month_bounds(year, month):
    start_date = date(year, month, 1)

    end_date = date(
        year,
        month,
        calendar.monthrange(year, month)[1],
    )

    return start_date, end_date


@transaction.atomic
def create_company(**company_data):
    company = Company.objects.create(
        **company_data
    )

    initialize_company_periods(company)

    return company


@transaction.atomic
def initialize_company_periods(company):
    current_date = company_local_date(company)

    start_date, end_date = month_bounds(
        current_date.year,
        current_date.month,
    )

    period, _ = FiscalPeriod.objects.get_or_create(
        company=company,
        year=current_date.year,
        month=current_date.month,
        defaults={
            "start_date": start_date,
            "end_date": end_date,
            "status": FiscalPeriod.Status.OPEN,
        },
    )

    for document_type, prefix in (
        DOCUMENT_PREFIXES.items()
    ):
        DocumentSequence.objects.get_or_create(
            company=company,
            document_type=document_type,
            year=current_date.year,
            defaults={
                "prefix": prefix,
                "next_number": 1,
                "padding": 6,
            },
        )

    return period


def assert_open_fiscal_period(
    company,
    transaction_date,
):
    if hasattr(transaction_date, "date"):
        transaction_date = (
            transaction_date.date()
        )

    period = FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=transaction_date,
        end_date__gte=transaction_date,
    ).first()

    if period is None:
        raise ValidationError(
            "No fiscal period exists for this date."
        )

    if period.status != FiscalPeriod.Status.OPEN:
        raise ValidationError(
            f"Fiscal period {period.year}-"
            f"{period.month:02d} is "
            f"{period.status.lower()}."
        )

    return period


def _record_period_audit(
    user,
    action,
    period,
    before_data,
    reason,
):
    from apps.audit.services import (
        record_audit_event,
    )

    record_audit_event(
        user=user,
        action=action,
        instance=period,
        before_data=before_data,
        after_data={
            "status": period.status,
        },
        reason=reason,
    )


@transaction.atomic
def close_fiscal_period(
    *,
    period_id,
    user,
    reason,
):
    period = (
        FiscalPeriod.objects
        .select_for_update()
        .select_related("company")
        .filter(pk=period_id)
        .first()
    )

    if period is None:
        raise ValidationError(
            "Fiscal period was not found."
        )

    require_owner(
        user,
        period.company,
    )

    if period.status != FiscalPeriod.Status.OPEN:
        raise ValidationError(
            "Only an open period can be closed."
        )

    if not reason or not reason.strip():
        raise ValidationError(
            "A closing reason is required."
        )

    before_data = {
        "status": period.status,
    }

    period.status = FiscalPeriod.Status.CLOSED
    period.closed_by = user
    period.closed_at = timezone.now()
    period.notes = reason.strip()

    period.save(
        update_fields=[
            "status",
            "closed_by",
            "closed_at",
            "notes",
            "updated_at",
        ]
    )

    _record_period_audit(
        user,
        "fiscal_period.closed",
        period,
        before_data,
        reason,
    )

    return period


@transaction.atomic
def lock_fiscal_period(
    *,
    period_id,
    user,
    reason,
):
    period = (
        FiscalPeriod.objects
        .select_for_update()
        .select_related("company")
        .filter(pk=period_id)
        .first()
    )

    if period is None:
        raise ValidationError(
            "Fiscal period was not found."
        )

    require_owner(
        user,
        period.company,
    )

    if (
        period.status
        != FiscalPeriod.Status.CLOSED
    ):
        raise ValidationError(
            "Only a closed period can be locked."
        )

    if not reason or not reason.strip():
        raise ValidationError(
            "A locking reason is required."
        )

    before_data = {
        "status": period.status,
    }

    period.status = FiscalPeriod.Status.LOCKED
    period.locked_by = user
    period.locked_at = timezone.now()
    period.notes = reason.strip()

    period.save(
        update_fields=[
            "status",
            "locked_by",
            "locked_at",
            "notes",
            "updated_at",
        ]
    )

    _record_period_audit(
        user,
        "fiscal_period.locked",
        period,
        before_data,
        reason,
    )

    return period


def next_document_number(
    *,
    company,
    document_type,
    year=None,
):
    if document_type not in DOCUMENT_PREFIXES:
        raise ValidationError(
            "Unsupported document type."
        )

    year = (
        year
        or company_local_date(company).year
    )

    # SQLite can briefly raise "database is locked"
    # under concurrent requests.
    for attempt in range(8):
        try:
            with transaction.atomic():
                (
                    DocumentSequence.objects
                    .get_or_create(
                        company=company,
                        document_type=document_type,
                        year=year,
                        defaults={
                            "prefix": (
                                DOCUMENT_PREFIXES[
                                    document_type
                                ]
                            ),
                            "next_number": 1,
                            "padding": 6,
                        },
                    )
                )

                sequence = (
                    DocumentSequence.objects
                    .select_for_update()
                    .get(
                        company=company,
                        document_type=document_type,
                        year=year,
                    )
                )

                allocated_number = (
                    sequence.next_number
                )

                (
                    DocumentSequence.objects
                    .filter(pk=sequence.pk)
                    .update(
                        next_number=(
                            F("next_number") + 1
                        )
                    )
                )

                return (
                    f"{sequence.prefix}-{year}-"
                    f"{allocated_number:0{sequence.padding}d}"
                )

        except OperationalError as exc:
            is_sqlite_lock = (
                "locked" in str(exc).lower()
            )

            if (
                not is_sqlite_lock
                or attempt == 7
            ):
                raise

            time.sleep(
                0.02 * (attempt + 1)
            )