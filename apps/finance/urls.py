"""Rotas do financeiro (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.finance.views import CommissionViewSet, FinanceChoicesView, TransactionViewSet

router = DefaultRouter()
router.register("finance/transactions", TransactionViewSet, basename="transaction")
router.register("finance/commissions", CommissionViewSet, basename="commission")

urlpatterns = [
    path("finance/choices/", FinanceChoicesView.as_view(), name="finance-choices"),
    path("", include(router.urls)),
]
