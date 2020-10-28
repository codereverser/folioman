from rest_framework.serializers import ModelSerializer

from folioman.models import Portfolio


class PortfolioSerializer(ModelSerializer):
    class Meta:
        model = Portfolio
        fields = "__all__"
