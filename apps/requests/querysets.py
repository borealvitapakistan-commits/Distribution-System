from django.db import models

from apps.distributors.querysets import is_approved_distributor


class StockRequestQuerySet(models.QuerySet):
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
