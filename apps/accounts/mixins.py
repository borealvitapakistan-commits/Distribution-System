from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


class RoleRequiredMixin(LoginRequiredMixin):
    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        user = request.user

        if user.is_authenticated:
            if not user.is_active:
                raise PermissionDenied(
                    "Your account is inactive."
                )

            if not user.company_id:
                raise PermissionDenied(
                    "Your account has no company."
                )

            if user.role not in self.allowed_roles:
                raise PermissionDenied(
                    "You are not allowed to access this page."
                )

        return super().dispatch(
            request,
            *args,
            **kwargs,
        )


class OwnerRequiredMixin(RoleRequiredMixin):
    allowed_roles = ("OWNER",)


class DistributorRequiredMixin(RoleRequiredMixin):
    allowed_roles = ("DISTRIBUTOR",)