import casparser
from rest_framework import parsers
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from .models import Portfolio
from .serializers import PortfolioSerializer
from .importers.cas import import_cas


class CASParserView(APIView):
    parser_classes = [parsers.MultiPartParser]

    def post(self, request: Request, _=None):
        ret = {"status": "FAIL", "message": "Unknown Error", "data": []}
        data = request.data
        if "password" in data and "file" in data:
            password = data["password"]
            if not isinstance(password, str):
                ret.update(message="Invalid password")
                raise ValidationError(detail={"message": ret["message"]})

            try:
                output = casparser.read_cas_pdf(data["file"], password)
                return Response({"status": "OK", "message": "Success", "data": output})
            except Exception as e:
                ret["message"] = str(e)
        return Response(ret)


# noinspection PyUnusedLocal,PyShadowingBuiltins
class PortfolioViewSet(ModelViewSet):

    serializer_class = PortfolioSerializer

    def get_queryset(self):
        return Portfolio.objects.filter(user_id=self.request.user.id)

    def list(self, request, *args, **kwargs):
        data = {
            "user": request.user.username,
            "email": request.user.email,
            "portfolios": self.serializer_class(self.get_queryset(), many=True).data,
        }
        return Response(data)

    @action(["POST"], detail=False)
    def search(self, request, format=None):
        email = request.data.get("email")
        try:
            obj = Portfolio.objects.get(email=email)
            if obj.user_id == request.user.id:
                return Response(PortfolioSerializer(obj).data)
            else:
                raise PermissionDenied
        except Portfolio.DoesNotExist:
            raise NotFound


@api_view(["POST"])
def cas_import(request):

    ret = {
        "status": "err",
        "message": "Unknown error",
        "num_folios": 0,
        "transactions": {"total": 0, "added": 0},
    }

    pdf_data = request.data
    data: casparser.CASParserDataType = pdf_data.get("data", {}) or {}

    try:
        result = import_cas(data, request.user.id)
    except ValueError as e:
        raise ValidationError({"detail": str(e)})
    else:
        ret.update(status="OK", message="Success", **result)
    return Response(ret)
