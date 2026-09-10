"""Endpoints de estoque e vendas."""

from __future__ import annotations

from django.db.models import F, Prefetch, QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.permissions import IsOwner, IsOwnerOrBarber
from apps.core.services import audit
from apps.inventory.models import Sale, SaleItem, StockItem, StockMovement
from apps.inventory.serializers import (
    SaleCancelSerializer,
    SaleCompleteSerializer,
    SaleCreateSerializer,
    SaleSerializer,
    StockItemSerializer,
    StockMovementCreateSerializer,
    StockMovementSerializer,
)
from apps.inventory.services import cancel_sale, complete_sale, create_sale, register_movement


@extend_schema_view(
    list=extend_schema(tags=["Estoque"], summary="Saldos de estoque por filial"),
)
class StockItemViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Saldos. A quantidade só muda por movimentação; aqui altera-se o mínimo."""

    serializer_class = StockItemSerializer
    permission_classes = [IsOwnerOrBarber]
    filterset_fields = ("branch", "product")
    search_fields = ("product__name", "product__sku", "product__barcode")
    ordering_fields = ("quantity", "product__name")
    ordering = ("product__name",)

    def get_queryset(self) -> QuerySet[StockItem]:
        queryset = StockItem.objects.select_related("product", "branch")
        if self.request.query_params.get("below_minimum") == "true":
            queryset = queryset.filter(quantity__lte=F("minimum_stock"))
        return queryset

    @extend_schema(
        tags=["Estoque"],
        summary="Produtos abaixo do estoque mínimo",
        responses={200: StockItemSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="low-stock")
    def low_stock(self, request: Request) -> Response:
        queryset = (
            StockItem.objects.select_related("product", "branch")
            .filter(quantity__lte=F("minimum_stock"), product__is_active=True)
            .order_by("quantity")
        )
        branch_id = request.query_params.get("branch")
        if branch_id:
            queryset = queryset.filter(branch_id=branch_id)
        return Response(StockItemSerializer(queryset, many=True).data)


@extend_schema_view(
    list=extend_schema(tags=["Estoque"], summary="Histórico de movimentações"),
)
class StockMovementViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Movimentações de estoque. O histórico é imutável."""

    serializer_class = StockMovementSerializer
    permission_classes = [IsOwnerOrBarber]
    filterset_fields = ("branch", "product", "type")
    ordering_fields = ("created_at",)
    ordering = ("-created_at",)

    def get_queryset(self) -> QuerySet[StockMovement]:
        return StockMovement.objects.select_related("product", "branch", "created_by")

    @extend_schema(
        tags=["Estoque"],
        summary="Registra entrada, ajuste, perda ou devolução",
        request=StockMovementCreateSerializer,
        responses={201: StockMovementSerializer},
    )
    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = StockMovementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        movement = register_movement(
            product_id=data["product_id"],
            branch_id=data["branch_id"],
            movement_type=data["type"],
            quantity=data["quantity"],
            unit_cost=data.get("unit_cost"),
            reason=data.get("reason", ""),
            created_by=request.user,
        )
        audit.log_create(movement, user=request.user)
        return Response(StockMovementSerializer(movement).data, status=201)


@extend_schema_view(
    list=extend_schema(tags=["Vendas"], summary="Lista vendas de produtos"),
    retrieve=extend_schema(tags=["Vendas"], summary="Detalha uma venda"),
)
class SaleViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Vendas de produtos, avulsas ou vinculadas a um atendimento."""

    serializer_class = SaleSerializer
    permission_classes = [IsOwnerOrBarber]
    filterset_fields = ("branch", "status", "barber", "client")
    ordering_fields = ("created_at", "completed_at", "total")
    ordering = ("-created_at",)

    def get_queryset(self) -> QuerySet[Sale]:
        queryset = Sale.objects.select_related(
            "branch", "client__user", "barber__user"
        ).prefetch_related(Prefetch("items", queryset=SaleItem.objects.select_related("product")))
        user = self.request.user
        if user.is_barber:
            barber = getattr(user, "barber_profile", None)
            queryset = queryset.filter(barber=barber) if barber else queryset.none()
        return queryset

    @extend_schema(
        tags=["Vendas"],
        summary="Registra uma venda",
        request=SaleCreateSerializer,
        responses={201: SaleSerializer},
    )
    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = SaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        barber_id = data.get("barber_id")
        if request.user.is_barber and barber_id is None:
            barber = getattr(request.user, "barber_profile", None)
            barber_id = barber.pk if barber else None

        sale = create_sale(
            branch_id=data["branch_id"],
            items=data["items"],
            client_id=data.get("client_id"),
            barber_id=barber_id,
            appointment_id=data.get("appointment_id"),
            discount_amount=data.get("discount_amount") or 0,
            notes=data.get("notes", ""),
            created_by=request.user,
        )

        payment_method = data.get("payment_method")
        if payment_method:
            sale = complete_sale(sale=sale, payment_method=payment_method, created_by=request.user)

        audit.log_create(sale, user=request.user)
        sale.refresh_from_db()
        return Response(SaleSerializer(sale, context={"request": request}).data, status=201)

    @extend_schema(
        tags=["Vendas"],
        summary="Conclui uma venda em aberto",
        request=SaleCompleteSerializer,
        responses={200: SaleSerializer},
    )
    @action(detail=True, methods=["post"])
    def complete(self, request: Request, pk: str | None = None) -> Response:
        sale = self.get_object()
        serializer = SaleCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = complete_sale(
            sale=sale,
            payment_method=serializer.validated_data["payment_method"],
            created_by=request.user,
        )
        return Response(SaleSerializer(sale, context={"request": request}).data)

    @extend_schema(
        tags=["Vendas"],
        summary="Cancela uma venda (OWNER)",
        request=SaleCancelSerializer,
        responses={200: SaleSerializer},
    )
    @action(detail=True, methods=["post"], permission_classes=[IsOwner])
    def cancel(self, request: Request, pk: str | None = None) -> Response:
        sale = self.get_object()
        serializer = SaleCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = cancel_sale(
            sale=sale,
            reason=serializer.validated_data.get("reason", ""),
            created_by=request.user,
        )
        return Response(SaleSerializer(sale, context={"request": request}).data)
