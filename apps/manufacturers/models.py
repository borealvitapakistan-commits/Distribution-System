from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import AuditedModel
from apps.core.querysets import OwnerManagedQuerySet


class Manufacturer(AuditedModel):
    name = models.CharField(max_length=200, unique=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def clean(self):
        super().clean()

        self.name = self.name.strip()

        if not self.name:
            raise ValidationError(
                {"name": "Manufacturer name is required."}
            )

    def __str__(self):
        return self.name
