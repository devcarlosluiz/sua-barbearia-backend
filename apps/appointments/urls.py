"""Rotas de agendamentos (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.appointments.views import (
    AppointmentHistoryView,
    AppointmentViewSet,
    AvailableSlotsView,
)

router = DefaultRouter()
router.register("appointments", AppointmentViewSet, basename="appointment")

urlpatterns = [
    path(
        "appointments/available-slots/",
        AvailableSlotsView.as_view(),
        name="appointment-available-slots",
    ),
    path("appointments/history/", AppointmentHistoryView.as_view(), name="appointment-history"),
    path("", include(router.urls)),
]
