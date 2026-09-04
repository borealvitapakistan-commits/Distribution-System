from rest_framework.permissions import BasePermission


class IsActiveCompanyUser(BasePermission):
    message = "An Active Company Account is Required"

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.company_id
        )



class IsOwner(BasePermission):
    message = "Only an Owner can perform this action."

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.company_id
            and user.is_owner
        )


class IsDistributor(BasePermission):
    message = "Only a Distributor can perform this action."

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.company_id
            and user.is_distributor
        )