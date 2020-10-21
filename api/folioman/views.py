import casparser
from casparser.exceptions import ParserException
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Portfolio
from rest_framework import parsers


class CASParserView(APIView):
    parser_classes = [parsers.MultiPartParser]

    def post(self, request: Request, _=None):
        ret = {
            'status': 'FAIL',
            'message': 'Unknown Error',
            'data': []
        }
        data = request.data
        if 'password' in data and 'file' in data:
            password = data['password']
            if not isinstance(password, str):
                ret.update(message='Invalid password')
                raise ValidationError(detail={'message': ret['message']})

            try:
                output = casparser.read_cas_pdf(data['file'], password)
                return Response({
                    'status': 'OK',
                    'message': 'Success',
                    'data': output
                })
            except Exception as e:
                ret['message'] = str(e)
        return Response(ret)


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
