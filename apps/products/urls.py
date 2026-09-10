"""Rotas de produtos (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.products.views import ProductCategoryViewSet, ProductViewSet

router = DefaultRouter()
router.register("products", ProductViewSet, basename="product")
router.register("product-categories", ProductCategoryViewSet, basename="product-category")

urlpatterns = [path("", include(router.urls))]
