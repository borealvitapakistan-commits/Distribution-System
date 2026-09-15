from django.views.generic import TemplateView


class AccountPendingView(TemplateView):
    template_name = "accounts/account_pending.html"
