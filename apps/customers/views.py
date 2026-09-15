from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import OwnerRequiredMixin

from .forms import CustomerForm
from .models import Customer
from .services import create_customer, update_customer


class CustomerListView(OwnerRequiredMixin, ListView):
    template_name = "customers/customer_list.html"
    context_object_name = "customers"

    def get_queryset(self):
        return Customer.objects.for_user(self.request.user)


class CustomerCreateView(OwnerRequiredMixin, View):
    template_name = "customers/customer_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": CustomerForm()},
        )

    def post(self, request):
        form = CustomerForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            customer = create_customer(
                actor=request.user,
                **form.cleaned_data,
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Customer created successfully.")
        return redirect("customer-detail", pk=customer.pk)


class CustomerDetailView(OwnerRequiredMixin, DetailView):
    template_name = "customers/customer_detail.html"
    context_object_name = "customer"

    def get_queryset(self):
        return Customer.objects.for_user(self.request.user)


class CustomerUpdateView(OwnerRequiredMixin, View):
    template_name = "customers/customer_form.html"

    def get_object(self):
        return (
            Customer.objects.for_user(self.request.user)
            .filter(pk=self.kwargs["pk"])
            .first()
        )

    def get(self, request, pk):
        customer = self.get_object()
        if customer is None:
            raise Http404
        return render(
            request,
            self.template_name,
            {
                "form": CustomerForm(instance=customer),
                "object": customer,
            },
        )

    def post(self, request, pk):
        customer = self.get_object()
        if customer is None:
            raise Http404

        form = CustomerForm(request.POST, instance=customer)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "object": customer},
            )

        try:
            customer = update_customer(
                actor=request.user,
                customer_id=customer.pk,
                **form.cleaned_data,
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {"form": form, "object": customer},
            )

        messages.success(request, "Customer updated successfully.")
        return redirect("customer-detail", pk=customer.pk)
