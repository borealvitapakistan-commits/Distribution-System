from django.contrib import messages
from django.contrib.auth.decorators import (
    login_required,
)
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import (
    TemplateView,
    UpdateView,
)

from apps.accounts.mixins import (
    DistributorRequiredMixin,
    OwnerRequiredMixin,
)
from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.audit.services import record_audit_event

from .forms import BrandForm
from .models import Brand


@login_required
def dashboard_redirect(request):
    user = request.user

    if not user.is_active:
        raise PermissionDenied(
            "An active account is required."
        )

    if user.is_owner:
        return redirect("owner-dashboard")

    if user.is_distributor:
        return redirect("distributor-dashboard")

    raise PermissionDenied(
        "Your user role is invalid."
    )


class OwnerDashboardView(OwnerRequiredMixin, TemplateView):
    template_name = "dashboards/owner.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(
            **kwargs
        )

        context["owner_count"] = User.objects.filter(
            role=User.Role.OWNER,
            is_active=True,
        ).count()

        context["distributor_count"] = User.objects.filter(
            role=User.Role.DISTRIBUTOR,
            is_active=True,
        ).count()

        context["pending_count"] = User.objects.filter(
            role=User.Role.DISTRIBUTOR,
            is_active=False,
        ).count()

        context["audit_count"] = (
            AuditEvent.objects
            .for_user(self.request.user)
            .count()
        )

        return context


class DistributorDashboardView(DistributorRequiredMixin, TemplateView,):
    template_name = "dashboards/distributor.html"


class BrandProfileView(OwnerRequiredMixin, UpdateView):
    """Brand is an effective singleton: get the one row, or create it
    on first visit, rather than requiring a separate "create" step."""

    model = Brand
    form_class = BrandForm
    template_name = "core/brand_profile.html"

    success_url = reverse_lazy(
        "brand-profile"
    )

    def get_object(self, queryset=None):
        brand = Brand.objects.for_user(self.request.user).first()

        if brand is None:
            brand = Brand.objects.create(name="My Brand")

        return brand

    def form_valid(self, form):
        changed_fields = list(form.changed_data)

        before_data = {
            field: str(
                getattr(
                    self.object,
                    field,
                    "",
                )
            )
            for field in changed_fields
        }

        response = super().form_valid(form)

        after_data = {
            field: str(
                getattr(
                    self.object,
                    field,
                    "",
                )
            )
            for field in changed_fields
        }

        if changed_fields:
            record_audit_event(
                user=self.request.user,
                action="brand.updated",
                instance=self.object,
                before_data=before_data,
                after_data=after_data,
                request=self.request,
            )

        messages.success(self.request, "Brand profile updated successfully.")

        return response
