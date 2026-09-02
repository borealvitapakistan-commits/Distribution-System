from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.generic import TemplateView
from apps.accounts.mixins import DistributorRequiredMixin, OwnerRequiredMixin


@login_required
def dashboard_redirect(request):
    if request.user.is_owner:
        return redirect("owner-dashboard")

    if request.user.is_distributor:
        return redirect("distributor-dashboard")

    raise PermissionDenied("Your User Role is Invalid")


class OwnerDashboardView(OwnerRequiredMixin, TemplateView):
    template_name = ("dashboards/owner.html")


class DistributorDashboardView(DistributorRequiredMixin, TemplateView):
    template_name = ("dashboards/distributor.html")
