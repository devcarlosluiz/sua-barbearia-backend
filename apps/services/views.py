"""Endpoints do catálogo de serviços."""

from __future__ import annotations

from django.db.models import Count, QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets

from apps.core.mixins import AuditableViewSetMixin
from apps.core.permissions import IsOwnerOrReadOnly
from apps.services.models import Service, ServiceCategory
from apps.services.serializers import ServiceCategorySerializer, ServiceSerializer


@extend_schema_view(
    list=extend_schema(tags=["Serviços"], summary="Lista categorias de serviço"),
    retrieve=extend_schema(tags=["Serviços"], summary="Detalha uma categoria"),
)
class ServiceCategoryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ServiceCategorySerializer
    permission_classes = [IsOwnerOrReadOnly]
    filterset_fields = ("is_active",)
    search_fields = ("name",)
    ordering_fields = ("display_order", "name")
    ordering = ("display_order", "name")

    def get_queryset(self) -> QuerySet[ServiceCategory]:
        queryset = ServiceCategory.objects.annotate(
            services_count=Count("services", filter=None, distinct=True)
        )
        if self.request.user.is_authenticated and self.request.user.is_client:
            queryset = queryset.filter(is_active=True)
        return queryset


@extend_schema_view(
    list=extend_schema(tags=["Serviços"], summary="Lista os serviços da Sua Barbearia"),
    retrieve=extend_schema(tags=["Serviços"], summary="Detalha um serviço"),
    create=extend_schema(tags=["Serviços"], summary="Cria um serviço (OWNER)"),
    update=extend_schema(tags=["Serviços"], summary="Atualiza um serviço (OWNER)"),
    partial_update=extend_schema(tags=["Serviços"], summary="Atualiza parcialmente (OWNER)"),
    destroy=extend_schema(tags=["Serviços"], summary="Remove um serviço (OWNER)"),
)
class ServiceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """CRUD de serviços. Clientes e barbeiros só leem os ativos."""

    serializer_class = ServiceSerializer
    permission_classes = [IsOwnerOrReadOnly]
    filterset_fields = ("is_active", "category")
    search_fields = ("name", "description")
    ordering_fields = ("display_order", "name", "price", "duration_minutes")
    ordering = ("display_order", "name")

    def get_queryset(self) -> QuerySet[Service]:
        queryset = Service.objects.select_related("category")
        user = self.request.user
        if user.is_authenticated and not (user.is_owner or user.is_superuser):
            queryset = queryset.filter(is_active=True)
        barber_id = self.request.query_params.get("barber")
        if barber_id:
            queryset = queryset.filter(
                barber_services__barber_id=barber_id, barber_services__is_active=True
            ).distinct()
        branch_id = self.request.query_params.get("branch")
        if branch_id:
            queryset = queryset.filter(
                barber_services__barber__branches__id=branch_id,
                barber_services__is_active=True,
                barber_services__barber__is_active=True,
            ).distinct()
        return queryset
