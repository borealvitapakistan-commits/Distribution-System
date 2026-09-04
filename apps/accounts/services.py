from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token
from .models import User


ROLE_GROUPS = {
    User.Role.DISTRIBUTOR: "Distributor",
    User.Role.OWNER: "Owner"
}



def require_owner(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication is required.")

    if not user.is_active or not user.company_id:
        raise PermissionDenied("An active company account is required.")

    if not user.is_owner:
        raise PermissionDenied("Only an Owner can perform this action.")



def sync_user_group(user):
    group_name = ROLE_GROUPS.get(user.role)

    if not group_name:
        raise ValidationError("Only OWNER and DISTRIBUTOR roles are allowed.")

    role_groups = Group.objects.filter(name__in=ROLE_GROUPS.values())
    user.groups.remove(*role_groups)
    group, _ = Group.objects.get_or_create(name=group_name)

    user.groups.add(group)


def invalidate_user_sessions(user):
    sessions = Session.objects.filter(expire_date__gte=timezone.now())

    for session in sessions.iterator():
        session_data = session.get_decoded()

        if str(session_data.get("_auth_user_id")) == str(user.pk):
            session.delete()

    Token.objects.filter(user=user).delete()



@transaction.atomic
def invite_distributor(*, actor, email, temporary_password, first_name="", last_name="", phone=""):
    require_owner(actor)

    distributor = User.objects.create_user(
        email=email,
        password=temporary_password,
        company=actor.company,
        role=User.Role.DISTRIBUTOR,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        is_active=False,
        must_change_password=True,
    )

    sync_user_group(distributor)

    return distributor


@transaction.atomic
def approve_distributor(*, user, distributor_id):
    require_owner(user)

    distributor = (
        User.objects
        .select_for_update()
        .get(pk=distributor_id)
    )

    if distributor.company_id != user.company_id:
        raise PermissionDenied("Cross-company approval is prohibited.")

    if not distributor.is_distributor:
        raise ValidationError("The selected user is not a Distributor.")

    distributor.is_active = True
    distributor.must_change_password = True

    distributor.save(
        update_fields=[
            "is_active",
            "must_change_password",
        ]
    )

    return distributor


@transaction.atomic
def deactivate_user(*, user, target_user_id):
    require_owner(user)

    target = (
        User.objects
        .select_for_update()
        .get(pk=target_user_id)
    )

    if target.company_id != user.company_id:
        raise PermissionDenied("Cross-company deactivation is prohibited.")

    if target.pk == user.pk:
        raise ValidationError("You cannot deactivate your own account.")

    target.is_active = False
    target.save(update_fields=["is_active"])

    invalidate_user_sessions(target)

    return target