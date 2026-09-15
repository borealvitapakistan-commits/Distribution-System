from django.db import models


class OwnerManagedQuerySet(models.QuerySet):
    """Base queryset for records only the Owner manages.

    This system runs for a single brand — there's no "which tenant"
    dimension to scope by, only "is this user allowed to see Owner-managed
    records at all."
    """

    def for_user(self, user):
        if not (
            user
            and user.is_authenticated
            and user.is_active
        ):
            return self.none()

        if not getattr(user, "is_owner", False):
            return self.none()

        return self.all()
