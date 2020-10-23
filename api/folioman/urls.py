from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns
from . import views

urlpatterns = [
   path('profile', views.ListPortfolios.as_view(), name='list_portfolios'),
   path('casparser', views.CASParserView.as_view(), name='casparser'),
]
