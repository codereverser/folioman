from rest_framework.serializers import ModelSerializer

from .models import Portfolio


class PortfolioSerializer(ModelSerializer):
    class Meta:
        model = Portfolio
        fields = "__all__"
