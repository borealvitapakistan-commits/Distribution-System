from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import DistributorRequiredMixin, OwnerRequiredMixin

from .forms import (
    AgreementForm,
    ProductRateFormSet,
    ReasonForm,
    SignAgreementForm,
    product_rate_rows,
)
from .models import Agreement
from .services import (
    cancel_agreement,
    create_agreement,
    decline_agreement,
    sign_agreement,
)


def _error_text(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


class AgreementQuerysetMixin:
    def get_queryset(self):
        return (
            Agreement.objects
            .for_user(self.request.user)
            .select_related("distributor_profile", "owner_signed_by")
            .prefetch_related("product_rates__product")
        )


class OwnerAgreementListView(OwnerRequiredMixin, AgreementQuerysetMixin, ListView):
    template_name = "agreements/agreement_list.html"
    context_object_name = "agreements"
    extra_context = {"is_owner_view": True}


class OwnerAgreementCreateView(OwnerRequiredMixin, View):
    template_name = "agreements/agreement_form.html"

    def _render(self, request, form, rate_formset, service_error=None):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "rate_formset": rate_formset,
                "service_error": service_error,
            },
        )

    def get(self, request):
        initial = {}

        if request.GET.get("distributor"):
            initial["distributor_profile"] = request.GET["distributor"]

        return self._render(
            request,
            AgreementForm(initial=initial),
            ProductRateFormSet(prefix="rates"),
        )

    def post(self, request):
        form = AgreementForm(request.POST)
        rate_formset = ProductRateFormSet(request.POST, prefix="rates")

        if not (form.is_valid() and rate_formset.is_valid()):
            return self._render(request, form, rate_formset)

        try:
            agreement = create_agreement(
                actor=request.user,
                product_rates=product_rate_rows(rate_formset),
                **form.cleaned_data,
            )
        except (PermissionDenied, ValidationError) as exc:
            return self._render(request, form, rate_formset, _error_text(exc))

        messages.success(
            request,
            f"{agreement.agreement_number} sent to {agreement.distributor_profile.name} for signature.",
        )
        return redirect("owner-agreement-detail", pk=agreement.pk)


class OwnerAgreementDetailView(OwnerRequiredMixin, AgreementQuerysetMixin, DetailView):
    template_name = "agreements/agreement_detail.html"
    context_object_name = "agreement"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_owner_view"] = True
        context["cancel_form"] = ReasonForm()
        return context


class OwnerAgreementCancelView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        form = ReasonForm(request.POST)

        if not form.is_valid():
            messages.error(request, "A reason is required to cancel an agreement.")
            return redirect("owner-agreement-detail", pk=pk)

        try:
            cancel_agreement(
                actor=request.user,
                agreement_id=pk,
                reason=form.cleaned_data["reason"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, _error_text(exc))
            return redirect("owner-agreement-detail", pk=pk)

        messages.success(request, "Agreement cancelled.")
        return redirect("owner-agreement-detail", pk=pk)


class DistributorAgreementListView(DistributorRequiredMixin, AgreementQuerysetMixin, ListView):
    template_name = "agreements/agreement_list.html"
    context_object_name = "agreements"


class DistributorAgreementDetailView(DistributorRequiredMixin, AgreementQuerysetMixin, DetailView):
    template_name = "agreements/agreement_detail.html"
    context_object_name = "agreement"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sign_form"] = SignAgreementForm(
            initial={"signature": self.request.user.get_full_name()}
        )
        context["decline_form"] = ReasonForm()
        return context


class DistributorAgreementSignView(DistributorRequiredMixin, View):
    def post(self, request, pk):
        get_object_or_404(Agreement.objects.for_user(request.user), pk=pk)
        form = SignAgreementForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Type your full name to sign the agreement.")
            return redirect("distributor-agreement-detail", pk=pk)

        try:
            sign_agreement(
                actor=request.user,
                agreement_id=pk,
                signature=form.cleaned_data["signature"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, _error_text(exc))
            return redirect("distributor-agreement-detail", pk=pk)

        messages.success(request, "Agreement signed — its prices now apply to your orders.")
        return redirect("distributor-agreement-detail", pk=pk)


class DistributorAgreementDeclineView(DistributorRequiredMixin, View):
    def post(self, request, pk):
        get_object_or_404(Agreement.objects.for_user(request.user), pk=pk)
        form = ReasonForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Tell the Owner why you're declining.")
            return redirect("distributor-agreement-detail", pk=pk)

        try:
            decline_agreement(
                actor=request.user,
                agreement_id=pk,
                reason=form.cleaned_data["reason"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, _error_text(exc))
            return redirect("distributor-agreement-detail", pk=pk)

        messages.success(request, "Agreement declined — the Owner has been told why.")
        return redirect("distributor-agreement-detail", pk=pk)
