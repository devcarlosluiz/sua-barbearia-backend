"""Rotas de avaliações (prefixo: /api/v1/)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.reviews.views import ReviewViewSet

router = DefaultRouter()
router.register("reviews", ReviewViewSet, basename="review")

urlpatterns = [path("", include(router.urls))]
