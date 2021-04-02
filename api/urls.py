"""folioman URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path, re_path

from rest_framework_simplejwt.views import (
    token_obtain_pair,
    token_refresh,
    token_verify,
)

from views import LogoutView

urlpatterns = [
    path("admin/", admin.site.urls),
    re_path(r"^api/auth/login", token_obtain_pair),
    re_path(r"^api/auth/refresh", token_refresh),
    re_path(r"^api/auth/verify", token_verify),
    re_path(r"^api/auth/logout", LogoutView.as_view()),
    path("api/mutualfunds/", include("mutualfunds.urls")),
]
