from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsDistributor

from .models import DistributorStockBalance
from .serializers import DistributorStockBalanceSerializer


class DistributorStockBalanceListAPIView(APIView):
    permission_classes = [IsDistributor]

    def get(self, request):
        balances = (
            DistributorStockBalance.objects
            .for_user(request.user)
            .select_related("product", "location")
            .filter(quantity__gt=0)
        )

        return Response(
            DistributorStockBalanceSerializer(balances, many=True).data
        )
