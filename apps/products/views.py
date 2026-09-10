"""Endpoints de produtos."""

from __future__ import annotations

from django.db.models import Prefetch, QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets

from apps.core.mixins import AuditableViewSetMixin
from apps.core.permissions import IsOwnerOrBarber, IsOwnerOrReadOnly
from apps.inventory.models import StockItem
from apps.products.models import Product, ProductCategory
from apps.products.serializers import ProductCategorySerializer, ProductSerializer


@extend_schema_view(
    list=extend_schema(tags=["Produtos"], summary="Lista categorias de produto"),
)
class ProductCategoryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ProductCategorySerializer
    permission_classes = [IsOwnerOrReadOnly]
    queryset = ProductCategory.objects.all()
    filterset_fields = ("is_active",)
    search_fields = ("name",)
    ordering = ("name",)


@extend_schema_view(
    list=extend_schema(tags=["Produtos"], summary="Lista produtos"),
    retrieve=extend_schema(tags=["Produtos"], summary="Detalha um produto"),
    create=extend_schema(tags=["Produtos"], summary="Cadastra um produto (OWNER)"),
    update=extend_schema(tags=["Produtos"], summary="Atualiza um produto (OWNER)"),
)
class ProductViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Catálogo de produtos, com saldo de estoque por filial."""

    serializer_class = ProductSerializer
    permission_classes = [IsOwnerOrBarber]
    filterset_fields = ("is_active", "category")
    search_fields = ("name", "sku", "barcode", "description")
    ordering_fields = ("name", "sale_price", "created_at")
    ordering = ("name",)

    def get_queryset(self) -> QuerySet[Product]:
        queryset = Product.objects.select_related("category").prefetch_related(
            Prefetch("stock_items", queryset=StockItem.objects.select_related("branch"))
        )
        if not (self.request.user.is_owner or self.request.user.is_superuser):
            queryset = queryset.filter(is_active=True)
        return queryset

    def _assert_owner(self) -> None:
        from apps.core.exceptions import BusinessError

        user = self.request.user
        if not (user.is_owner or user.is_superuser):
            raise BusinessError(
                "Apenas o proprietário pode gerenciar o catálogo de produtos.",
                code="PERMISSION_DENIED",
                status_code=403,
            )

    def perform_create(self, serializer) -> None:
        self._assert_owner()
        super().perform_create(serializer)

    def perform_update(self, serializer) -> None:
        self._assert_owner()
        super().perform_update(serializer)

    def perform_destroy(self, instance) -> None:
        self._assert_owner()
        super().perform_destroy(instance)
