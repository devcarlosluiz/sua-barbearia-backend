"""Rotas de barbeiros (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.barbers.views import (
    BarberViewSet,
    SpecialWorkingHourViewSet,
    TimeOffViewSet,
    WorkingHourViewSet,
)

router = DefaultRouter()
router.register("barbers", BarberViewSet, basename="barber")
router.register("working-hours", WorkingHourViewSet, basename="working-hour")
router.register("special-hours", SpecialWorkingHourViewSet, basename="special-hour")
router.register("time-offs", TimeOffViewSet, basename="time-off")

urlpatterns = [path("", include(router.urls))]
