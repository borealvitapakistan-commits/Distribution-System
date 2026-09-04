from django.contrib import messages
from django.contrib.auth.decorators import (
    login_required,
)
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import (
    ListView,
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

from .forms import CompanyForm
from .models import (
    Company,
    DocumentSequence,
    FiscalPeriod,
)


@login_required
def dashboard_redirect(request):
    user = request.user

    if (
        not user.is_active
        or not user.company_id
        or not user.company.active
    ):
        raise PermissionDenied(
            "An active company account is required."
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

        user = self.request.user

        periods = FiscalPeriod.objects.for_user(
            user
        )

        context["current_period"] = (
            periods
            .filter(
                status=FiscalPeriod.Status.OPEN
            )
            .first()
        )

        company_users = User.objects.filter(
            company_id=user.company_id
        )

        context["owner_count"] = (
            company_users
            .filter(
                role=User.Role.OWNER,
                is_active=True,
            )
            .count()
        )

        context["distributor_count"] = (
            company_users
            .filter(
                role=User.Role.DISTRIBUTOR,
                is_active=True,
            )
            .count()
        )

        context["pending_count"] = (
            company_users
            .filter(
                role=User.Role.DISTRIBUTOR,
                is_active=False,
            )
            .count()
        )

        context["sequence_count"] = (
            DocumentSequence.objects
            .for_user(user)
            .count()
        )

        context["audit_count"] = (
            AuditEvent.objects
            .for_user(user)
            .count()
        )

        return context


class DistributorDashboardView(DistributorRequiredMixin, TemplateView,):
    template_name = "dashboards/distributor.html"


class CompanyProfileView(OwnerRequiredMixin, UpdateView):
    model = Company
    form_class = CompanyForm
    template_name = "core/company_profile.html"
    
    success_url = reverse_lazy(
        "company-profile"
    )

    def get_object(self, queryset=None):
        return (
            Company.objects
            .for_user(self.request.user)
            .get()
        )

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
                action="company.updated",
                instance=self.object,
                before_data=before_data,
                after_data=after_data,
                request=self.request,
            )

        messages.success(self.request, "Company profile updated successfully.")

        return response


class FiscalPeriodListView(OwnerRequiredMixin, ListView):
    template_name = "core/fiscal_period_list.html"
    context_object_name = "periods"

    def get_queryset(self):
        return (
            FiscalPeriod.objects
            .for_user(self.request.user)
            .select_related(
                "closed_by",
                "locked_by",
            )
        )


class DocumentSequenceListView(OwnerRequiredMixin, ListView,):
    template_name = "core/document_sequence_list.html"
    context_object_name = "sequences"

    def get_queryset(self):
        return (DocumentSequence.objects.for_user(self.request.user))