from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import (
    DistributorRequiredMixin,
    OwnerRequiredMixin,
)

from .forms import (
    DeclineRequestForm,
    FulfillRequestItemForm,
    OwnerCommentForm,
    StockRequestItemFormSet,
)
from .models import StockRequest
from .services import (
    add_owner_comment,
    create_stock_request,
    decline_request,
    fulfill_request_item,
)


def request_item_rows(formset):
    rows = []

    for form in formset.forms:
        if not form.cleaned_data:
            continue

        if form.cleaned_data.get("DELETE"):
            continue

        if not form.cleaned_data.get("product"):
            continue

        rows.append(
            {
                "product": form.cleaned_data["product"],
                "quantity_requested": form.cleaned_data["quantity_requested"],
            }
        )

    return rows


class StockRequestCreateView(DistributorRequiredMixin, View):
    template_name = "requests/stock_request_form.html"

    def get(self, request):
        formset = StockRequestItemFormSet(instance=StockRequest())

        return render(
            request,
            self.template_name,
            {"formset": formset},
        )

    def post(self, request):
        formset = StockRequestItemFormSet(
            request.POST,
            instance=StockRequest(),
        )

        if not formset.is_valid():
            return render(
                request,
                self.template_name,
                {"formset": formset},
            )

        try:
            create_stock_request(
                actor=request.user,
                items=request_item_rows(formset),
            )
        except (PermissionDenied, ValidationError) as exc:
            return render(
                request,
                self.template_name,
                {"formset": formset, "service_error": exc},
            )

        messages.success(request, "Request submitted successfully.")
        return redirect("distributor-stock-request-list")


class DistributorStockRequestListView(DistributorRequiredMixin, ListView):
    template_name = "requests/distributor_stock_request_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        return (
            StockRequest.objects
            .for_user(self.request.user)
            .prefetch_related("items__product")
        )


class OwnerStockRequestListView(OwnerRequiredMixin, ListView):
    template_name = "requests/owner_stock_request_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        return (
            StockRequest.objects
            .for_user(self.request.user)
            .select_related("distributor_profile")
            .prefetch_related("items")
        )


class OwnerStockRequestDetailView(OwnerRequiredMixin, DetailView):
    template_name = "requests/owner_stock_request_detail.html"
    context_object_name = "stock_request"

    def get_queryset(self):
        return (
            StockRequest.objects
            .for_user(self.request.user)
            .select_related("distributor_profile")
            .prefetch_related("items__product")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["fulfill_form"] = FulfillRequestItemForm()
        context["decline_form"] = DeclineRequestForm()
        context["comment_form"] = OwnerCommentForm(
            initial={"comment": self.object.owner_comment}
        )
        return context


class FulfillStockRequestItemView(OwnerRequiredMixin, View):
    def post(self, request, pk, item_id):
        form = FulfillRequestItemForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Please provide a valid quantity and warehouse.")
            return redirect("owner-stock-request-detail", pk=pk)

        try:
            fulfill_request_item(
                actor=request.user,
                item_id=item_id,
                quantity=form.cleaned_data["quantity"],
                from_location=form.cleaned_data["from_location"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("owner-stock-request-detail", pk=pk)

        messages.success(request, "Stock sent to Distributor successfully.")
        return redirect("owner-stock-request-detail", pk=pk)


class DeclineStockRequestView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        form = DeclineRequestForm(request.POST)

        if not form.is_valid():
            messages.error(request, "A comment is required to decline a request.")
            return redirect("owner-stock-request-detail", pk=pk)

        try:
            decline_request(
                actor=request.user,
                request_id=pk,
                comment=form.cleaned_data["comment"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("owner-stock-request-detail", pk=pk)

        messages.success(request, "Request declined.")
        return redirect("owner-stock-request-detail", pk=pk)


class CommentStockRequestView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        form = OwnerCommentForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Could not save comment.")
            return redirect("owner-stock-request-detail", pk=pk)

        try:
            add_owner_comment(
                actor=request.user,
                request_id=pk,
                comment=form.cleaned_data["comment"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("owner-stock-request-detail", pk=pk)

        messages.success(request, "Comment saved.")
        return redirect("owner-stock-request-detail", pk=pk)
