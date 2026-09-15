from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import (
    invalidate_user_sessions,
    require_owner,
    sync_user_group,
)
from apps.audit.services import record_audit_event

from .models import OwnerProfile


@transaction.atomic
def create_owner(
    *,
    actor,
    email,
    temporary_password,
    name,
    first_name="",
    last_name="",
    phone="",
    notes="",
):
    """Create another Owner's login and profile together.

    Owner accounts double as Django admin logins, so a new Owner is
    active immediately (no approval step, unlike Distributors).
    """
    require_owner(actor)

    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError("A user with this email already exists.")

    owner = User.objects.create_user(
        email=email,
        password=temporary_password,
        role=User.Role.OWNER,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        is_active=True,
        is_staff=True,
        is_superuser=True,
        must_change_password=True,
    )

    sync_user_group(owner)

    profile = OwnerProfile(
        user=owner,
        name=name,
        phone=phone,
        notes=notes,
        created_by=actor,
        updated_by=actor,
    )
    profile.full_clean()
    profile.save()

    record_audit_event(
        user=actor,
        action="owner.created",
        instance=profile,
        after_data={
            "user_id": str(owner.pk),
            "name": profile.name,
        },
    )

    return owner


@transaction.atomic
def update_owner_profile(*, user, **data):
    """Self-service edit of the Owner's own account fields.

    Works whether or not the Owner has an `OwnerProfile` row — the root
    Owner created via `createsuperuser` never gets one, and still needs
    to be able to edit their own name/phone.
    """
    if not user.is_active or not user.is_owner:
        raise PermissionDenied("Only an active Owner can update this profile.")

    before = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
    }
    for field in ["first_name", "last_name", "phone"]:
        if field in data:
            setattr(user, field, data.pop(field))
    if data:
        raise ValidationError("One or more profile fields are not editable.")

    user.save(update_fields=["first_name", "last_name", "phone"])

    after = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
    }
    record_audit_event(
        user=user,
        action="owner.profile_updated",
        instance=user,
        before_data=before,
        after_data=after,
    )
    return user


@transaction.atomic
def deactivate_owner(*, actor, target_user_id):
    require_owner(actor)

    target = User.objects.select_for_update().get(pk=target_user_id)

    if target.pk == actor.pk:
        raise ValidationError("You cannot deactivate your own account.")

    if not target.is_owner:
        raise ValidationError("This action only applies to Owner accounts.")

    target.is_active = False
    target.save(update_fields=["is_active"])

    OwnerProfile.objects.filter(user=target).update(
        active=False,
        updated_by=actor,
        updated_at=timezone.now(),
    )

    invalidate_user_sessions(target)

    return target
