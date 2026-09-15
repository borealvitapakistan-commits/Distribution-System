from .models import AuditEvent


SENSITIVE_WORDS = {
    "password",
    "token",
    "secret",
    "api_key",
    "access_token",
    "refresh_token",
}



def redact_sensitive_data(data):
    if isinstance(data, dict):
        cleaned = {}

        for key, value in data.items():
            normalized_key = str(key).lower()

            if any(
                word in normalized_key
                for word in SENSITIVE_WORDS
            ):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = redact_sensitive_data(
                    value
                )

        return cleaned

    if isinstance(data, list):
        return [
            redact_sensitive_data(item)
            for item in data
        ]

    return data


def record_audit_event(*, user, action, instance, before_data=None, after_data=None, reason="", request=None):
    return AuditEvent.objects.create(
        actor=user,
        action=action,
        object_type=instance._meta.label_lower,
        object_id=str(instance.pk),
        object_label=str(instance),
        before_data=redact_sensitive_data(
            before_data or {}
        ),
        after_data=redact_sensitive_data(
            after_data or {}
        ),
        reason=reason,
        request_id=(
            request.headers.get("X-Request-ID", "")
            if request
            else ""
        ),
        ip_address=(
            request.META.get("REMOTE_ADDR")
            if request
            else None
        ),
        user_agent=(
            request.META.get("HTTP_USER_AGENT", "")
            if request
            else ""
        ),
    )