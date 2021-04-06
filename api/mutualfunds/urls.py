from django.urls import path
from rest_framework import routers
from rest_framework.urlpatterns import format_suffix_patterns
from . import views

router = routers.SimpleRouter()
router.register(r"portfolios", views.PortfolioViewSet, basename="portfolios")

urlpatterns = [
    path("casparser", views.CASParserView.as_view(), name="casparser"),
    path("cas/import", views.cas_import),
    path("portfolio_history", views.portfolio_value),
    path("portfolio_list", views.portfolio_schemes),
] + router.urls


urlpatterns = format_suffix_patterns(urlpatterns)
