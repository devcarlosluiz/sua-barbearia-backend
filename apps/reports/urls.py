"""Rotas de relatórios (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import path

from apps.reports.views import BarberDashboardView, ClientDashboardView, OwnerDashboardView

urlpatterns = [
    path("dashboard/owner/", OwnerDashboardView.as_view(), name="dashboard-owner"),
    path("dashboard/barber/", BarberDashboardView.as_view(), name="dashboard-barber"),
    path("dashboard/client/", ClientDashboardView.as_view(), name="dashboard-client"),
]
