from apps.core.querysets import OwnerManagedQuerySet


def is_approved_distributor(user):
    if not (
        user
        and user.is_authenticated
        and user.is_active
    ):
        return False

    if not user.is_distributor:
        return False

    profile = getattr(user, "distributor_profile", None)

    return bool(
        profile
        and profile.approval_status == "APPROVED"
    )


class DistributorProfileQuerySet(OwnerManagedQuerySet):
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
            return self.filter(user_id=user.pk)

        return self.none()
