from django.db import models
from django.db.models import F, Max, Q

from apps.distributors.querysets import is_approved_distributor


class PurchaseOrderQuerySet(models.QuerySet):
    def for_user(self, user):
        if not (
            user
            and user.is_authenticated
            and user.is_active
        ):
            return self.none()

        if user.is_owner:
            return self.all()

        if is_approved_distributor(user):
            return self.filter(
                distributor_profile=user.distributor_profile
            )

        return self.none()

    def unseen_by_owner(self):
        return self.filter(owner_viewed_at__isnull=True)

    def with_updates_for_owner(self):
        """Orders the Owner has opened before, where the Distributor has
        said something since — a counter-offer, or accepting the quote."""
        return (
            self.filter(owner_viewed_at__isnull=False)
            .annotate(
                last_distributor_step=Max(
                    "revisions__number", filter=Q(revisions__by_owner=False)
                )
            )
            .filter(last_distributor_step__gt=F("owner_seen_revision"))
        )

    def with_updates_for_distributor(self):
        """Orders where the Owner has said something the Distributor
        hasn't seen yet — a quote, a changed Purchase Order, the invoice."""
        return (
            self.annotate(
                last_owner_step=Max(
                    "revisions__number", filter=Q(revisions__by_owner=True)
                )
            )
            .filter(last_owner_step__gt=F("distributor_seen_revision"))
        )
