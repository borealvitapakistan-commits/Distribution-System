from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import (
    invalidate_user_sessions,
    require_owner,
    sync_user_group,
)
from apps.audit.services import record_audit_event

from .models import DistributorProfile


@transaction.atomic
def create_distributor(
    *,
    actor,
    email,
    temporary_password,
    name,
    first_name="",
    last_name="",
    phone="",
    territory="",
    address="",
    commission_percentage=Decimal("0"),
    upfront_payment_percentage=Decimal("0"),
):
    """Create the Distributor's login and business profile together.

    This is the single "Owner adds a Distributor" action: login details
    and business details (including commission) are collected on one
    screen and created atomically.
    """
    require_owner(actor)

    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError("A user with this email already exists.")

    distributor = User.objects.create_user(
        email=email,
        password=temporary_password,
        role=User.Role.DISTRIBUTOR,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        is_active=False,
        must_change_password=True,
    )

    sync_user_group(distributor)

    profile = DistributorProfile(
        user=distributor,
        name=name,
        territory=territory,
        address=address,
        commission_percentage=commission_percentage,
        upfront_payment_percentage=upfront_payment_percentage,
        approval_status=DistributorProfile.ApprovalStatus.PENDING,
        created_by=actor,
        updated_by=actor,
    )
    profile.full_clean()
    profile.save()

    record_audit_event(
        user=actor,
        action="distributor.created",
        instance=profile,
        after_data={
            "user_id": str(distributor.pk),
            "name": profile.name,
            "commission_percentage": str(profile.commission_percentage),
            "upfront_payment_percentage": str(profile.upfront_payment_percentage),
            "approval_status": profile.approval_status,
        },
    )

    return distributor


@transaction.atomic
def approve_distributor(*, user, distributor_id):
    require_owner(user)

    profile = (
        DistributorProfile.objects.select_for_update()
        .select_related("user")
        .filter(
            models.Q(pk=distributor_id)
            | models.Q(user_id=distributor_id)
        )
        .first()
    )

    if profile is None or profile.user is None:
        raise ValidationError(
            "A Distributor profile with an invited user was not found."
        )

    distributor = profile.user
    if not distributor.is_distributor:
        raise ValidationError("The selected user is not a Distributor.")

    if profile.approval_status not in {
        DistributorProfile.ApprovalStatus.PENDING,
        DistributorProfile.ApprovalStatus.SUSPENDED,
    }:
        raise ValidationError(
            "Only pending or suspended Distributor profiles can be approved."
        )

    distributor.is_active = True
    distributor.must_change_password = True
    distributor.save(update_fields=["is_active", "must_change_password"])

    profile.approval_status = DistributorProfile.ApprovalStatus.APPROVED
    profile.approved_by = user
    profile.approved_at = timezone.now()
    profile.updated_by = user
    profile.full_clean()
    profile.save(
        update_fields=[
            "approval_status",
            "approved_by",
            "approved_at",
            "updated_by",
            "updated_at",
        ]
    )

    record_audit_event(
        user=user,
        action="distributor.approved",
        instance=profile,
        after_data={
            "user_id": str(distributor.pk),
            "approval_status": profile.approval_status,
        },
    )

    return distributor


@transaction.atomic
def suspend_distributor(*, user, distributor_id, reason):
    require_owner(user)

    if not reason or not reason.strip():
        raise ValidationError("A suspension reason is required.")

    profile = (
        DistributorProfile.objects.select_for_update()
        .select_related("user")
        .filter(
            models.Q(pk=distributor_id)
            | models.Q(user_id=distributor_id)
        )
        .first()
    )
    if profile is None or profile.user is None:
        raise ValidationError("Distributor profile was not found.")
    if profile.approval_status != DistributorProfile.ApprovalStatus.APPROVED:
        raise ValidationError("Only an approved Distributor can be suspended.")

    before = profile.approval_status
    profile.approval_status = DistributorProfile.ApprovalStatus.SUSPENDED
    profile.updated_by = user
    profile.save(update_fields=["approval_status", "updated_by", "updated_at"])

    profile.user.is_active = False
    profile.user.save(update_fields=["is_active"])
    invalidate_user_sessions(profile.user)

    record_audit_event(
        user=user,
        action="distributor.suspended",
        instance=profile,
        before_data={"approval_status": before},
        after_data={"approval_status": profile.approval_status},
        reason=reason.strip(),
    )
    return profile


@transaction.atomic
def reject_distributor(*, user, distributor_id, reason):
    require_owner(user)

    if not reason or not reason.strip():
        raise ValidationError("A rejection reason is required.")

    profile = (
        DistributorProfile.objects.select_for_update()
        .select_related("user")
        .filter(
            models.Q(pk=distributor_id)
            | models.Q(user_id=distributor_id)
        )
        .first()
    )
    if profile is None:
        raise ValidationError("Distributor profile was not found.")
    if profile.approval_status != DistributorProfile.ApprovalStatus.PENDING:
        raise ValidationError("Only a pending Distributor can be rejected.")

    profile.approval_status = DistributorProfile.ApprovalStatus.REJECTED
    profile.updated_by = user
    profile.save(update_fields=["approval_status", "updated_by", "updated_at"])
    if profile.user_id:
        profile.user.is_active = False
        profile.user.save(update_fields=["is_active"])
        invalidate_user_sessions(profile.user)
    record_audit_event(
        user=user,
        action="distributor.rejected",
        instance=profile,
        after_data={"approval_status": profile.approval_status},
        reason=reason.strip(),
    )
    return profile


@transaction.atomic
def update_distributor_profile(*, user, profile, **data):
    if not user.is_active or not user.is_distributor:
        raise PermissionDenied("Only an active Distributor can update this profile.")
    if profile.user_id != user.pk:
        raise PermissionDenied("You can update only your own Distributor profile.")
    if profile.approval_status != DistributorProfile.ApprovalStatus.APPROVED:
        raise PermissionDenied("The Distributor profile is not approved.")

    before = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "address": profile.address,
        "territory": profile.territory,
        "notes": profile.notes,
    }
    for field in ["first_name", "last_name", "phone"]:
        if field in data:
            setattr(user, field, data.pop(field))
    for field in ["address", "territory", "notes"]:
        if field in data:
            setattr(profile, field, data.pop(field))
    if data:
        raise ValidationError("One or more profile fields are not editable.")

    user.save(update_fields=["first_name", "last_name", "phone"])
    profile.updated_by = user
    profile.full_clean()
    profile.save(
        update_fields=[
            "address",
            "territory",
            "notes",
            "updated_by",
            "updated_at",
        ]
    )
    after = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "address": profile.address,
        "territory": profile.territory,
        "notes": profile.notes,
    }
    record_audit_event(
        user=user,
        action="distributor.profile_updated",
        instance=profile,
        before_data=before,
        after_data=after,
    )
    return profile
