"""Rotas de fidelidade (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.loyalty.views import (
    LoyaltyAccountViewSet,
    LoyaltyRewardViewSet,
    LoyaltyTransactionViewSet,
)

router = DefaultRouter()
router.register("loyalty/accounts", LoyaltyAccountViewSet, basename="loyalty-account")
router.register("loyalty/transactions", LoyaltyTransactionViewSet, basename="loyalty-transaction")
router.register("loyalty/rewards", LoyaltyRewardViewSet, basename="loyalty-reward")

urlpatterns = [path("", include(router.urls))]
