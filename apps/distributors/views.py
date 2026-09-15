from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView, ListView, UpdateView

from apps.accounts.mixins import DistributorRequiredMixin, OwnerRequiredMixin
from apps.accounts.models import User
from apps.audit.services import record_audit_event

from .forms import DistributorInvitationForm, DistributorProfileForm
from .services import (
    approve_distributor,
    create_distributor,
    reject_distributor,
    suspend_distributor,
)


class DistributorListView(OwnerRequiredMixin, ListView):
    template_name = "distributors/distributor_list.html"
    context_object_name = "distributors"

    def get_queryset(self):
        return (
            User.objects
            .select_related("distributor_profile")
            .filter(role=User.Role.DISTRIBUTOR)
            .order_by("email")
        )


class DistributorInviteView(OwnerRequiredMixin, FormView):
    template_name = "distributors/distributor_invite.html"
    form_class = DistributorInvitationForm
    success_url = reverse_lazy("distributor-list")

    def form_valid(self, form):
        try:
            create_distributor(
                actor=self.request.user,
                **form.cleaned_data,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(
            self.request,
            "Distributor added. "
            "The account is pending approval.",
        )

        return super().form_valid(form)


class ApproveDistributorView(OwnerRequiredMixin, View):
    def post(self, request, user_id):
        try:
            approve_distributor(
                user=request.user,
                distributor_id=user_id,
            )

            messages.success(
                request,
                "Distributor approved successfully.",
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc))

        return redirect("distributor-list")


class SuspendDistributorView(OwnerRequiredMixin, View):
    def post(self, request, user_id):
        reason = request.POST.get("reason", "").strip()

        try:
            suspend_distributor(
                user=request.user,
                distributor_id=user_id,
                reason=reason,
            )

            messages.success(
                request,
                "Distributor suspended successfully.",
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc))

        return redirect("distributor-list")


class RejectDistributorView(OwnerRequiredMixin, View):
    def post(self, request, user_id):
        reason = request.POST.get("reason", "").strip()

        try:
            reject_distributor(
                user=request.user,
                distributor_id=user_id,
                reason=reason,
            )

            messages.success(
                request,
                "Distributor rejected successfully.",
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc))

        return redirect("distributor-list")


class DistributorProfileView(
    DistributorRequiredMixin,
    UpdateView,
):
    model = User
    form_class = DistributorProfileForm
    template_name = "distributors/distributor_profile.html"
    success_url = reverse_lazy("distributor-profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        before_data = {
            field: str(
                getattr(self.object, field, "")
            )
            for field in form.changed_data
        }

        response = super().form_valid(form)

        after_data = {
            field: str(
                getattr(self.object, field, "")
            )
            for field in form.changed_data
        }

        if form.changed_data:
            record_audit_event(
                user=self.request.user,
                action="distributor.profile_updated",
                instance=self.object,
                before_data=before_data,
                after_data=after_data,
                request=self.request,
            )

        messages.success(
            self.request,
            "Profile updated successfully.",
        )

        return response
