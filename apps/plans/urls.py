"""Rotas de planos e assinaturas (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.plans.views import (
    AsaasWebhookView,
    PlanViewSet,
    SubscriptionInvoiceViewSet,
    SubscriptionViewSet,
)

router = DefaultRouter()
router.register("plans", PlanViewSet, basename="plan")
router.register("subscriptions", SubscriptionViewSet, basename="subscription")
router.register(
    "subscription-invoices", SubscriptionInvoiceViewSet, basename="subscription-invoice"
)

urlpatterns = [
    path(
        "webhooks/asaas/",
        AsaasWebhookView.as_view(),
        name="asaas-webhook",
    ),
    path("", include(router.urls)),
]
