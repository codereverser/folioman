from django.urls import path
from rest_framework import routers
from rest_framework.urlpatterns import format_suffix_patterns

from . import views

router = routers.SimpleRouter()
router.register(r"portfolio", views.PortfolioViewSet, basename="portfolios")

urlpatterns = [
    path("casparser", views.CASParserView.as_view(), name="casparser"),
    path("cas/import", views.cas_import),
] + router.urls


urlpatterns = format_suffix_patterns(urlpatterns)
