"""Rotas de filiais (prefixo: /api/v1/branches/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.branches.views import BranchViewSet, PublicBranchListView

router = DefaultRouter()
router.register("branches", BranchViewSet, basename="branch")

urlpatterns = [
    # Precisa vir antes do router para não colidir com `branches/<pk>/`.
    path("branches/public/", PublicBranchListView.as_view(), name="branch-public-list"),
    path("", include(router.urls)),
]
