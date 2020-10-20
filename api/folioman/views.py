from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Portfolio


class ListPortfolios(APIView):
    """
    List all portfolios belonging to user.
    """
    permission_classes = (IsAuthenticated, )

    def get(self, request: Request, _=None):
        pfs = Portfolio.objects.filter(user_id=request.user.id)
        data = {
            "user": request.user.username,
            "email": request.user.email,
        }
        items = []
        for portfolio in pfs:
            items.append({
                "name": portfolio.name,
                "email": portfolio.email,
                "pan": portfolio.pan
            })
        data["portfolios"] = items
        return Response(data)
