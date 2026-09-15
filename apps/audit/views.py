from django.views.generic import ListView
from apps.accounts.mixins import OwnerRequiredMixin
from .models import AuditEvent


class AuditEventListView(OwnerRequiredMixin, ListView):
    template_name = "audit/audit_event_list.html"
    context_object_name = "events"
    paginate_by = 25

    def get_queryset(self):
        return (
            AuditEvent.objects
            .for_user(self.request.user)
            .select_related("actor")
        )