from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

class RoleRequiredMixin(LoginRequiredMixin):
    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if (
                request.user.role
                not in self.allowed_roles
            ):
                raise PermissionDenied(
                    "You are not allowed "
                    "to access this page."
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