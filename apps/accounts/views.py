from django.contrib import messages
from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.audit.services import (
    record_audit_event,
)

from .forms import (
    DistributorInvitationForm,
    DistributorProfileForm,
)
from .mixins import (
    DistributorRequiredMixin,
    OwnerRequiredMixin,
)
from .models import User
from .services import (
    approve_distributor,
    deactivate_user,
    invite_distributor,
)


class OwnerUserListView(OwnerRequiredMixin, ListView):
    template_name = "accounts/owner_user_list.html"
    context_object_name = "users"

    def get_queryset(self):
        return (
            User.objects
            .filter(
                company_id=(
                    self.request.user.company_id
                )
            )
            .order_by(
                "role",
                "email",
            )
        )


class DistributorInviteView(OwnerRequiredMixin, FormView):
    template_name = "accounts/distributor_invite.html"
    form_class = DistributorInvitationForm
    success_url = reverse_lazy(
        "owner-user-list"
    )

    def form_valid(self, form):
        try:
            invite_distributor(
                actor=self.request.user,
                **form.cleaned_data,
            )

        except ValidationError as exc:
            form.add_error(
                None,
                exc,
            )

            return self.form_invalid(form)

        messages.success(
            self.request,
            "Distributor invitation created. "
            "The account is pending approval.",
        )

        return super().form_valid(form)


class ApproveDistributorView(OwnerRequiredMixin,View):
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

        except (
            ValidationError,
            PermissionDenied,
        ) as exc:
            messages.error(
                request,
                str(exc),
            )

        return redirect(
            "owner-user-list"
        )


class DeactivateUserView(OwnerRequiredMixin, View):
    def post(
        self,
        request,
        user_id,
    ):
        try:
            deactivate_user(
                user=request.user,
                target_user_id=user_id,
            )

            messages.success(
                request,
                "User deactivated successfully.",
            )

        except (
            ValidationError,
            PermissionDenied,
        ) as exc:
            messages.error(
                request,
                str(exc),
            )

        return redirect(
            "owner-user-list"
        )


class AccountPendingView(TemplateView):
    template_name = "accounts/account_pending.html"
    


class DistributorProfileView(DistributorRequiredMixin, UpdateView):
    model = User
    form_class = DistributorProfileForm
    template_name = "accounts/distributor_profile.html"
    success_url = reverse_lazy(
        "distributor-profile"
    )


    def get_object(self, queryset=None):
        return self.request.user


    def form_valid(self, form):
        before_data = {
            field: str(
                getattr(
                    self.object,
                    field,
                    "",
                )
            )
            for field in form.changed_data
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
            for field in form.changed_data
        }

        if form.changed_data:
            record_audit_event(
                user=self.request.user,
                action=(
                    "distributor.profile_updated"
                ),
                instance=self.object,
                before_data=before_data,
                after_data=after_data,
                request=self.request,
            )

        messages.success(self.request, "Profile updated successfully.")

        return response