from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from mutualfunds.models import Portfolio as MFPortfolio

class UserView(APIView):
    def get(self, request, format=None):
        user = request.user
        if not user.is_authenticated:
            raise exceptions.PermissionDenied

        mf_portfolios = [
            {"name": x.name, "value": 0.0}
            for x in MFPortfolio.objects.filter(user=request.user).all()]

        data = {
            "user": {
                "username": user.username,
                "firstname": user.first_name,
                "lastname": user.last_name,
                "email": user.email,
                "portfolios": {
                    "mutualfunds": mf_portfolios
                }
            }
        }
        return Response(status=status.HTTP_200_OK, data=data)
