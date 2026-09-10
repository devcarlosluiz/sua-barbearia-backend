"""Rotas de pagamentos (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.payments.views import PaymentViewSet

router = DefaultRouter()
router.register("payments", PaymentViewSet, basename="payment")

urlpatterns = [path("", include(router.urls))]
