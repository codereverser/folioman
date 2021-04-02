from django.utils.text import gettext_lazy as _
from rest_framework import exceptions, permissions, serializers, status
from rest_framework.fields import empty
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError


class RefreshTokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    default_error_messages = {"bad_token": _("Token is invalid or expired")}

    def __init__(self, instance=None, data=empty, **kwargs):
        if isinstance(data, dict) and "refresh" in data:
            self.token = data["refresh"]
        else:
            self.fail("bad_input")
        super().__init__(instance=instance, data=data, **kwargs)

    def save(self, **kwargs):
        try:
            RefreshToken(self.token).blacklist()
        except TokenError:
            self.fail("bad_token")


class LogoutView(GenericAPIView):
    serializer_class = RefreshTokenSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, *args):
        sz = self.get_serializer(data=request.data)
        sz.is_valid(raise_exception=True)
        sz.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserView(APIView):
    def get(self, request, format=None):
        user = request.user
        if not user.is_authenticated:
            raise exceptions.PermissionDenied
        data = {
            "user": {
                "username": user.username,
                "firstname": user.first_name,
                "lastname": user.last_name,
                "email": user.email,
            }
        }
        return Response(status=status.HTTP_200_OK, data=data)
