from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.forms import (
    PasswordResetForm,
)

from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import (
    AllowAny,
    IsAuthenticated,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    LoginSerializer,
    PasswordResetSerializer,
    UserSerializer,
)


class LoginAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(
            data=request.data,
            context={
                "request": request,
            },
        )

        serializer.is_valid(
            raise_exception=True
        )

        user = serializer.validated_data[
            "user"
        ]

        token, _ = Token.objects.get_or_create(
            user=user
        )

        return Response(
            {
                "token": token.key,
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class LogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(
            user=request.user
        ).delete()

        logout(request)

        return Response(
            status=status.HTTP_204_NO_CONTENT
        )


class PasswordResetAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetSerializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        form = PasswordResetForm(
            data={
                "email": (
                    serializer.validated_data[
                        "email"
                    ]
                )
            }
        )

        if form.is_valid():
            form.save(
                request=request,
                use_https=request.is_secure(),
                from_email=(
                    settings.DEFAULT_FROM_EMAIL
                ),
            )

        return Response(
            {
                "detail": (
                    "If the account exists, "
                    "a reset email has been sent."
                )
            }
        )


class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            UserSerializer(
                request.user
            ).data
        )