"""Rotas do catálogo de serviços (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.services.views import ServiceCategoryViewSet, ServiceViewSet

router = DefaultRouter()
router.register("services", ServiceViewSet, basename="service")
router.register("service-categories", ServiceCategoryViewSet, basename="service-category")

urlpatterns = [path("", include(router.urls))]
