from django.contrib.auth.models import Permission, Group
from django.db.models.signals import (
    post_migrate,
    post_save,
    pre_save,
)
from django.dispatch import receiver
from .models import User


@receiver(post_migrate)
def create_role_groups(sender, **kwargs):
    if sender.label != "accounts":
        return

    owner_group, _ = Group.objects.get_or_create(name="Owner")
    Group.objects.get_or_create(name="Distributor")
    owner_group.permissions.set(Permission.objects.all())


@receiver(pre_save, sender=User)
def remember_previous_active_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_is_active = None
        return

    instance._previous_is_active = (
        sender.objects
        .filter(pk=instance.pk)
        .values_list(
            "is_active",
            flat=True,
        )
        .first()
    )


@receiver(post_save, sender=User)
def enforce_role_group_and_deactivation(sender, instance, **kwargs):
    role_groups = {
        User.Role.OWNER: "Owner",
        User.Role.DISTRIBUTOR: "Distributor",
    }

    group_name = role_groups.get(
        instance.role
    )

    if group_name:
        existing_role_groups = Group.objects.filter(
            name__in=role_groups.values()
        )

        instance.groups.remove(
            *existing_role_groups
        )

        group, _ = Group.objects.get_or_create(
            name=group_name
        )

        instance.groups.add(group)

    if (
        getattr(
            instance,
            "_previous_is_active",
            None,
        )
        is True
        and not instance.is_active
    ):
        from .services import (
            invalidate_user_sessions,
        )

        invalidate_user_sessions(instance)