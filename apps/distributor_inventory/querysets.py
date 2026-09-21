from datetime import date

from django.db import models
from django.db.models import Case, DateField, F, Value, When


class DistributorStockQuerySet(models.QuerySet):
    """Scopes rows to the requesting Distributor's own profile only —
    the Distributor-side mirror of apps.core.querysets.OwnerManagedQuerySet.
    Owners have no access to this data at all."""

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


class DistributorStockBatchQuerySet(DistributorStockQuerySet):
    def available(self):
        return self.filter(quantity_remaining__gt=0)

    def fefo_ordered(self):
        """Soonest-to-expire first; batches with no known expiry date sort
        last, then oldest received first."""
        return self.annotate(
            fefo_sort_date=Case(
                When(expiry_date__isnull=True, then=Value(date.max)),
                default=F("expiry_date"),
                output_field=DateField(),
            )
        ).order_by("fefo_sort_date", "received_date", "created_at")
