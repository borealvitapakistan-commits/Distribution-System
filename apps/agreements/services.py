from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import require_approved_distributor, require_owner
from apps.audit.services import record_audit_event
from apps.distributors.models import DistributorProfile

from .models import Agreement, AgreementProductRate


def _as_percentage(value, label="Discount"):
    try:
        value = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValidationError(f"{label} must be a number.")

    if not Decimal("0") <= value <= Decimal("100"):
        raise ValidationError(f"{label} must be between 0 and 100.")

    return value


def _next_agreement_number():
    last = Agreement.objects.order_by("-created_at").first()
    next_seq = 1

    if last is not None:
        try:
            next_seq = int(last.agreement_number.split("-")[-1]) + 1
        except (ValueError, IndexError):
            next_seq = Agreement.objects.count() + 1

    return f"AGR-{next_seq:05d}"


def agreement_in_force(distributor_profile, day=None):
    """The signed agreement that prices this Distributor's orders on
    `day` (today by default), or None — then list prices apply."""
    day = day or timezone.localdate()

    return (
        Agreement.objects
        .in_force_on(day)
        .filter(distributor_profile=distributor_profile)
        .prefetch_related("product_rates")
        .order_by("-start_date")
        .first()
    )


def _require_no_overlap(distributor_profile, start_date, end_date):
    """Only one live (sent or signed) agreement may cover any given day,
    so there's never doubt about which percentage an order gets."""
    clash = (
        Agreement.objects
        .filter(
            distributor_profile=distributor_profile,
            status__in=[Agreement.Status.SENT, Agreement.Status.ACCEPTED],
            start_date__lte=end_date,
            end_date__gte=start_date,
        )
        .first()
    )

    if clash is not None:
        raise ValidationError(
            f"{clash.agreement_number} already covers "
            f"{clash.start_date:%b %d, %Y} – {clash.end_date:%b %d, %Y} "
            f"for {distributor_profile.name}. Cancel it first or pick other dates."
        )


@transaction.atomic
def create_agreement(
    *,
    actor,
    distributor_profile,
    discount_percentage,
    start_date,
    end_date,
    terms="",
    product_rates=(),
):
    """The Owner fills in the agreement form and sends it — sending is
    the Owner's signature. product_rates: [(product, percentage), ...]."""
    require_owner(actor)

    if distributor_profile.approval_status != DistributorProfile.ApprovalStatus.APPROVED:
        raise ValidationError("Agreements can only be sent to an approved Distributor.")

    discount_percentage = _as_percentage(discount_percentage)

    if not start_date or not end_date:
        raise ValidationError("Both a start date and an end date are required.")

    if end_date < start_date:
        raise ValidationError("The end date can't be before the start date.")

    _require_no_overlap(distributor_profile, start_date, end_date)

    rates = []
    seen = set()

    for product, percentage in product_rates:
        if product is None:
            continue

        if product.pk in seen:
            raise ValidationError(f"{product.name} is listed more than once.")

        seen.add(product.pk)
        rates.append((product, _as_percentage(percentage, f"{product.name}'s discount")))

    agreement = Agreement(
        agreement_number=_next_agreement_number(),
        distributor_profile=distributor_profile,
        discount_percentage=discount_percentage,
        start_date=start_date,
        end_date=end_date,
        terms=(terms or "").strip(),
        owner_signed_by=actor,
        owner_signed_at=timezone.now(),
        created_by=actor,
        updated_by=actor,
    )
    agreement.full_clean()
    agreement.save()

    AgreementProductRate.objects.bulk_create(
        [
            AgreementProductRate(
                agreement=agreement,
                product=product,
                discount_percentage=percentage,
                created_by=actor,
                updated_by=actor,
            )
            for product, percentage in rates
        ]
    )

    record_audit_event(
        user=actor,
        action="agreements.agreement_sent",
        instance=agreement,
        after_data={
            "agreement_number": agreement.agreement_number,
            "distributor_profile_id": str(distributor_profile.pk),
            "discount_percentage": str(discount_percentage),
            "start_date": str(start_date),
            "end_date": str(end_date),
            "product_rates": {str(p.pk): str(pct) for p, pct in rates},
        },
    )

    return agreement


def _get_for_update(agreement_id):
    agreement = (
        Agreement.objects
        .select_for_update()
        .select_related("distributor_profile")
        .filter(pk=agreement_id)
        .first()
    )

    if agreement is None:
        raise ValidationError("Agreement was not found.")

    return agreement


def _get_own_awaiting(actor, agreement_id):
    require_approved_distributor(actor)

    agreement = _get_for_update(agreement_id)

    if agreement.distributor_profile_id != actor.distributor_profile.pk:
        raise ValidationError("You can only respond to your own agreements.")

    if agreement.status != Agreement.Status.SENT:
        raise ValidationError("This agreement has already been answered.")

    return agreement


@transaction.atomic
def sign_agreement(*, actor, agreement_id, signature):
    """The Distributor signs by typing their full name. From then on,
    their orders dated inside the agreement's window get its prices."""
    agreement = _get_own_awaiting(actor, agreement_id)

    signature = (signature or "").strip()

    if not signature:
        raise ValidationError("Type your full name to sign the agreement.")

    if agreement.is_expired:
        raise ValidationError(
            "This agreement's end date has passed — ask the Owner for a new one."
        )

    agreement.status = Agreement.Status.ACCEPTED
    agreement.distributor_signature = signature
    agreement.distributor_signed_by = actor
    agreement.distributor_signed_at = timezone.now()
    agreement.updated_by = actor
    agreement.save(
        update_fields=[
            "status", "distributor_signature", "distributor_signed_by",
            "distributor_signed_at", "updated_at", "updated_by",
        ]
    )

    record_audit_event(
        user=actor,
        action="agreements.agreement_signed",
        instance=agreement,
        after_data={"signature": signature},
    )

    return agreement


@transaction.atomic
def decline_agreement(*, actor, agreement_id, reason):
    agreement = _get_own_awaiting(actor, agreement_id)

    reason = (reason or "").strip()

    if not reason:
        raise ValidationError("Tell the Owner why you're declining.")

    agreement.status = Agreement.Status.DECLINED
    agreement.response_note = reason
    agreement.updated_by = actor
    agreement.save(update_fields=["status", "response_note", "updated_at", "updated_by"])

    record_audit_event(
        user=actor,
        action="agreements.agreement_declined",
        instance=agreement,
        after_data={"reason": reason},
    )

    return agreement


@transaction.atomic
def cancel_agreement(*, actor, agreement_id, reason):
    """The Owner withdraws an agreement. Orders already placed keep the
    prices they got; new orders stop getting its discount."""
    require_owner(actor)

    agreement = _get_for_update(agreement_id)

    if agreement.status not in (Agreement.Status.SENT, Agreement.Status.ACCEPTED):
        raise ValidationError("Only a sent or signed agreement can be cancelled.")

    reason = (reason or "").strip()

    if not reason:
        raise ValidationError("A reason is required to cancel an agreement.")

    agreement.status = Agreement.Status.CANCELLED
    agreement.response_note = reason
    agreement.updated_by = actor
    agreement.save(update_fields=["status", "response_note", "updated_at", "updated_by"])

    record_audit_event(
        user=actor,
        action="agreements.agreement_cancelled",
        instance=agreement,
        after_data={"reason": reason},
    )

    return agreement
