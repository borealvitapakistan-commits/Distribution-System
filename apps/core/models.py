import uuid
from django.db import models
from django.conf import settings


class UUIDModel(models.Model):
    """Provides UUID a Primary Key"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    class Meta:
        abstract = True



class TimeStampedModel(models.Model):
    """Track Record creation and modification time"""

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True


class AuditedModel(UUIDModel, TimeStampedModel):
    """Tracks which user created and updated a record."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        abstract = True







