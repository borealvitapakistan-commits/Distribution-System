from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from rest_framework.authtoken.models import Token
from .models import User


ROLE_GROUPS = {
    User.Role.OWNER: "Owner"
}



def require_owner(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication is required.")

    if not user.is_active:
        raise PermissionDenied("An active account is required.")

    if not user.is_owner:
        raise PermissionDenied("Only an Owner can perform this action.")


def sync_user_group(user):
    group_name = ROLE_GROUPS.get(user.role)

    if not group_name:
        raise ValidationError("Only the OWNER role is allowed.")

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
