from rest_framework.serializers import ModelSerializer, SerializerMethodField

from .models import Portfolio, Transaction


class PortfolioSerializer(ModelSerializer):
    class Meta:
        model = Portfolio
        fields = ["id", "name", "email", "pan"]


class TransactionSerializer(ModelSerializer):
    folio = SerializerMethodField()

    class Meta:
        model = Transaction
        fields = ["date", "description", "sub_type", "amount", "nav", "units", "balance", "folio"]

    def get_folio(self, obj: Transaction):
        return obj.scheme.folio.number
