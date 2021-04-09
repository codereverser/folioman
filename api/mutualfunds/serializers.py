from rest_framework.serializers import ModelSerializer, SerializerMethodField

from .models import Portfolio, SchemeValue


class PortfolioSerializer(ModelSerializer):
    class Meta:
        model = Portfolio
        fields = ["id", "name", "email", "pan"]


class SchemeSerializer(ModelSerializer):
    name = SerializerMethodField()

    class Meta:
        model = SchemeValue
