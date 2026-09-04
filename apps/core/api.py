from django.core.exceptions import (
    PermissionDenied as DjangoPermissionDenied,
)
from django.core.exceptions import (
    ValidationError as DjangoValidationError,
)

from rest_framework import generics, status
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwner
from apps.audit.services import record_audit_event

from .models import Company, FiscalPeriod
from .serializers import (
    CompanySerializer,
    FiscalPeriodSerializer,
    PeriodActionSerializer,
)
from .services import close_fiscal_period



class CompanyDetailAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsOwner]

    def get_object(self):
        return (
            Company.objects
            .for_user(self.request.user)
            .get()
        )

    def perform_update(self, serializer):
        company = self.get_object()

        before_data = CompanySerializer(
            company
        ).data

        updated_company = serializer.save()

        record_audit_event(
            user=self.request.user,
            action="company.updated",
            instance=updated_company,
            before_data=before_data,
            after_data=serializer.data,
            request=self.request,
        )


class FiscalPeriodListAPIView(generics.ListAPIView):
    serializer_class = (FiscalPeriodSerializer)
    permission_classes = [IsOwner]

    def get_queryset(self):
        return (
            FiscalPeriod.objects
            .for_user(self.request.user)
            .select_related(
                "closed_by",
                "locked_by",
            )
        )


class FiscalPeriodCloseAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request, period_id):
        serializer = PeriodActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            period = close_fiscal_period(
                period_id=period_id,
                user=request.user,
                reason=(serializer.validated_data["reason"]),
            )

        except DjangoPermissionDenied as exc:
            raise PermissionDenied(str(exc)) from exc

        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc

        return Response(
            FiscalPeriodSerializer(period).data, status=status.HTTP_200_OK)