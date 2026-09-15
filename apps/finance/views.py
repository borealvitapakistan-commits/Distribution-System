from django.views.generic import TemplateView

from apps.accounts.mixins import OwnerRequiredMixin


class OwnerFinanceView(OwnerRequiredMixin, TemplateView):
    template_name = "finance/dashboard.html"
