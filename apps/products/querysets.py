from django.db import models


def active_user(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active
    )


class OwnerOnlyQuerySet(models.QuerySet):
    def for_user(self, user):
        if not active_user(user):
            return self.none()

        if user.is_owner:
            return self.all()

        return self.none()


class ProductQuerySet(OwnerOnlyQuerySet):
    def for_user(self, user):
        if not active_user(user):
            return self.none()

        if user.is_owner:
            return self.filter(archived_at__isnull=True)

        return self.none()


class ProductIngredientQuerySet(models.QuerySet):
    def for_user(self, user):
        if not active_user(user) or not user.is_owner:
            return self.none()

        return self.all()
