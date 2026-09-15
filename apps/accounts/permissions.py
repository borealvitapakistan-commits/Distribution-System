from rest_framework.permissions import BasePermission


class IsOwner(BasePermission):
    message = "Only an Owner can perform this action."

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.is_owner
        )


class IsDistributor(BasePermission):
    message = "Only a Distributor can perform this action."

    def has_permission(self, request, view):
        user = request.user

        basic = bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.is_distributor
        )
        if not basic:
            return False

        profile = getattr(user, "distributor_profile", None)

        return bool(
            profile
            and profile.approval_status == "APPROVED"
        )
