from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from . import views

urlpatterns = [
    path("me", views.UserView.as_view(), name="me"),
]


urlpatterns = format_suffix_patterns(urlpatterns)
