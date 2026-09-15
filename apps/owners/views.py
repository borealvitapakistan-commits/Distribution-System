from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView, ListView, UpdateView

from apps.accounts.mixins import OwnerRequiredMixin
from apps.accounts.models import User
from apps.audit.services import record_audit_event

from .forms import OwnerCreationForm, OwnerProfileForm
from .services import create_owner, deactivate_owner


class OwnerListView(OwnerRequiredMixin, ListView):
    template_name = "owners/owner_list.html"
    context_object_name = "owners"

    def get_queryset(self):
        return (
            User.objects
            .select_related("owner_profile")
            .filter(role=User.Role.OWNER)
            .order_by("email")
        )


class OwnerCreateView(OwnerRequiredMixin, FormView):
    template_name = "owners/owner_form.html"
    form_class = OwnerCreationForm
    success_url = reverse_lazy("owner-list")

    def form_valid(self, form):
        try:
            create_owner(
                actor=self.request.user,
                **form.cleaned_data,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(self.request, "Owner added successfully.")
        return super().form_valid(form)


class OwnerProfileView(OwnerRequiredMixin, UpdateView):
    model = User
    form_class = OwnerProfileForm
    template_name = "owners/owner_profile.html"
    success_url = reverse_lazy("owner-profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        before_data = {
            field: str(getattr(self.object, field, ""))
            for field in form.changed_data
        }

        response = super().form_valid(form)

        after_data = {
            field: str(getattr(self.object, field, ""))
            for field in form.changed_data
        }

        if form.changed_data:
            record_audit_event(
                user=self.request.user,
                action="owner.profile_updated",
                instance=self.object,
                before_data=before_data,
                after_data=after_data,
                request=self.request,
            )

        messages.success(self.request, "Profile updated successfully.")
        return response


class DeactivateOwnerView(OwnerRequiredMixin, View):
    def post(self, request, user_id):
        try:
            deactivate_owner(actor=request.user, target_user_id=user_id)
            messages.success(request, "Owner deactivated successfully.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc))

        return redirect("owner-list")
