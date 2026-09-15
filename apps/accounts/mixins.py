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

            if user.role not in self.allowed_roles:
                raise PermissionDenied(
                    "You are not allowed to access this page."
                )

            if user.is_distributor:
                profile = getattr(user, "distributor_profile", None)
                if profile is None:
                    raise PermissionDenied(
                        "Your Distributor profile is not configured."
                    )
                if profile.approval_status != "APPROVED":
                    raise PermissionDenied(
                        "Your Distributor profile is not approved."
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