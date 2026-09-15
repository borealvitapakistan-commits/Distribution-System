from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import AuditedModel
from apps.core.querysets import OwnerManagedQuerySet


class OwnerProfile(AuditedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owner_profile",
    )

    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
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
                {"name": "Owner name is required."}
            )

        if self.user_id and self.user.role != "OWNER":
            raise ValidationError(
                {
                    "user": (
                        "The linked user must be "
                        "an Owner."
                    )
                }
            )

    def __str__(self):
        return self.name
