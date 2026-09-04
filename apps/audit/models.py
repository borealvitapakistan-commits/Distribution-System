from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import (
    Company,
    TimeStampedModel,
    UUIDModel,
)
from apps.core.querysets import (
    CompanyScopedQuerySet,
)


class AuditEventQuerySet(CompanyScopedQuerySet):
    def update(self, **kwargs):
        raise ValidationError(
            "Audit events are immutable."
        )

    def delete(self):
        raise ValidationError(
            "Audit events cannot be deleted."
        )


class AuditEvent(UUIDModel, TimeStampedModel):
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )

    action = models.CharField(
        max_length=100
    )

    object_type = models.CharField(
        max_length=150
    )

    object_id = models.CharField(
        max_length=150,
        blank=True,
    )

    object_label = models.CharField(
        max_length=255,
        blank=True,
    )

    before_data = models.JSONField(
        default=dict,
        blank=True,
    )

    after_data = models.JSONField(
        default=dict,
        blank=True,
    )

    reason = models.TextField(
        blank=True
    )

    request_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
    )

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )

    user_agent = models.TextField(
        blank=True
    )

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        exists = (
            self.pk
            and type(self).objects
            .filter(pk=self.pk)
            .exists()
        )

        if exists:
            raise ValidationError(
                "Audit events are immutable."
            )

        return super().save(
            *args,
            **kwargs,
        )

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Audit events cannot be deleted."
        )

    def __str__(self):
        return (
            f"{self.action} - "
            f"{self.object_type} - "
            f"{self.created_at}"
        )