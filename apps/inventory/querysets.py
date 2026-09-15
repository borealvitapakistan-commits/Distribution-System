from apps.core.querysets import OwnerManagedQuerySet


class StockBalanceQuerySet(OwnerManagedQuerySet):
    def for_user(self, user):
        if not (
            user
            and user.is_authenticated
            and user.is_active
        ):
            return self.none()

        if user.is_owner:
            return self.all()

        profile = getattr(user, "distributor_profile", None)

        if profile is None or profile.approval_status != "APPROVED":
            return self.none()

        return self.filter(location__distributor_profile=profile)
