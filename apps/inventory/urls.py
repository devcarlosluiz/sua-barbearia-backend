"""Rotas de estoque e vendas (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.inventory.views import SaleViewSet, StockItemViewSet, StockMovementViewSet

router = DefaultRouter()
router.register("inventory/stock", StockItemViewSet, basename="stock-item")
router.register("inventory/movements", StockMovementViewSet, basename="stock-movement")
router.register("sales", SaleViewSet, basename="sale")

urlpatterns = [path("", include(router.urls))]
