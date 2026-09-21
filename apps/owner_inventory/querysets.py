from datetime import date

from django.db.models import Case, DateField, F, Value, When

from apps.core.querysets import OwnerManagedQuerySet


class StockBalanceQuerySet(OwnerManagedQuerySet):
    pass


class StockBatchQuerySet(OwnerManagedQuerySet):
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
