from decimal import Decimal
import itertools

import casparser
from django.db.models import F, Func
from rest_framework import parsers
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from .models import Portfolio, PortfolioValue, SchemeValue, NAVHistory
from .serializers import PortfolioSerializer
from .importers.cas import import_cas


class EpochMS(Func):
    function = "EXTRACT"
    template = "%(function)s('epoch' from %(expressions)s) * 1000"


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
                output = casparser.read_cas_pdf(data["file"], password, sort_transactions=True)
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


@api_view(["GET"])
def portfolio_value(request):
    # TODO: Add portfolio_id parameter
    qs = (
        PortfolioValue.objects.filter(portfolio_id=1)
        .annotate(ts=EpochMS(F("date")))
        .values_list("ts", "invested", "value")
        .order_by("date")
    )
    items = list(qs.all())
    s1 = [(x[0], x[1]) for x in items]
    s2 = [(x[0], x[2]) for x in items]
    output = {"invested": s1, "value": s2}
    return Response(output)


@api_view(["GET"])
def portfolio_schemes(request):
    pf = PortfolioValue.objects.filter(portfolio_id=1).latest()
    date = pf.date
    scheme_vals = SchemeValue.objects.filter(
        date=date, scheme__folio__portfolio_id=1
    ).select_related("scheme", "scheme__scheme", "scheme__folio")
    results = []
    portfolio_change = Decimal("0.0")
    for scheme_id, group in itertools.groupby(scheme_vals, lambda x: x.scheme.scheme_id):
        obj = None
        total_invested = Decimal("0.0")
        total_value = Decimal("0.0")
        total_units = Decimal("0.0")
        total_change = Decimal("0.0")
        try:
            nav0, nav1 = (
                NAVHistory.objects.filter(scheme_id=scheme_id)
                .order_by("-date")
                .values_list("nav", flat=True)[:2]
            )
        except ValueError:
            nav0, nav1 = Decimal("0.0"), Decimal("0.0")
        for item in group:
            if obj is None:
                obj = {
                    "name": item.scheme.scheme.name.capitalize(),
                    "nav0": nav0,
                    "nav1": nav1,
                    "folios": [],
                }
            obj["folios"].append(
                {
                    "folio": item.scheme.folio.number,
                    "invested": item.invested,
                    "units": item.balance,
                    "value": item.value,
                    "avg_nav": item.avg_nav,
                }
            )
            total_change += item.balance * (nav0 - nav1)
            total_invested += item.invested
            total_value += item.value
            total_units += item.balance
        portfolio_change += total_change
        if obj is not None:
            if total_units >= 1e-4:
                avg_nav = Decimal(str(round(total_invested / total_units, 4)))
            else:
                avg_nav = Decimal("0.0000")
            obj.update(
                invested=total_invested, units=total_units,
                value=total_value, avg_nav=avg_nav,
                change=total_change
            )
            results.append(obj)
    output = {
        "invested": pf.invested,
        "value": pf.value,
        "change": portfolio_change,
        "date": date,
        "schemes": results
    }
    return Response(output)


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
    except Exception as e:
        import traceback, sys

        _, _, tb = sys.exc_info()
        traceback.print_tb(tb)
        raise ValidationError({"detail": str(e)})
    else:
        ret.update(status="OK", message="Success", **result)
    return Response(ret)
