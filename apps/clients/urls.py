"""Rotas de clientes (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.clients.views import ClientViewSet

router = DefaultRouter()
router.register("clients", ClientViewSet, basename="client")

urlpatterns = [path("", include(router.urls))]
