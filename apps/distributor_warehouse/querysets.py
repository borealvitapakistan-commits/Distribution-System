from django.db import models


class DistributorOwnedQuerySet(models.QuerySet):
    """Scopes rows to the requesting Distributor's own profile only.
    Owners have no access to this data at all, and one Distributor never
    sees another's warehouses — this is the Distributor-side mirror of
    apps.core.querysets.OwnerManagedQuerySet."""

    def for_user(self, user):
        if not (
            user
            and user.is_authenticated
            and user.is_active
        ):
            return self.none()

        profile = getattr(user, "distributor_profile", None)

        if profile is None or profile.approval_status != "APPROVED":
            return self.none()

        return self.filter(distributor_profile=profile)
