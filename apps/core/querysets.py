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


def count_many(**querysets):
    """{name: count} for several querysets in a single database round
    trip, instead of one trip per .count()."""
    from django.core.exceptions import EmptyResultSet
    from django.db import connection

    parts, params = [], []
    for queryset in querysets.values():
        try:
            sql, query_params = queryset.order_by().values("pk").query.sql_with_params()
        except EmptyResultSet:  # e.g. .none(): nothing to count
            parts.append("0")
            continue
        parts.append(f"(SELECT COUNT(*) FROM ({sql}) AS counted)")
        params.extend(query_params)
    with connection.cursor() as cursor:
        cursor.execute("SELECT " + ", ".join(parts), params)
        row = cursor.fetchone()
    return dict(zip(querysets, row))
