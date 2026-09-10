"""Rotas de notificações (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.notifications.views import DeviceTokenViewSet, NotificationViewSet

router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")
router.register("device-tokens", DeviceTokenViewSet, basename="device-token")

urlpatterns = [path("", include(router.urls))]
