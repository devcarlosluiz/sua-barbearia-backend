"""Serializers de estoque e vendas."""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.inventory.models import Sale, SaleItem, StockItem, StockMovement, StockMovementType
from apps.payments.models import PaymentMethod
from apps.products.serializers import ProductSummarySerializer


class StockItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    is_below_minimum = serializers.BooleanField(read_only=True)

    class Meta:
        model = StockItem
        fields = (
            "id",
            "product",
            "product_name",
            "product_sku",
            "branch",
            "branch_name",
            "quantity",
            "minimum_stock",
            "is_below_minimum",
            "updated_at",
        )
        read_only_fields = ("id", "quantity", "updated_at")


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    type_display = serializers.CharField(source="get_type_display", read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.full_name", read_only=True, default=None
    )

    class Meta:
        model = StockMovement
        fields = (
            "id",
            "uuid",
            "product",
            "product_name",
            "branch",
            "branch_name",
            "type",
            "type_display",
            "quantity",
            "previous_quantity",
            "new_quantity",
            "unit_cost",
            "reason",
            "created_by_name",
            "created_at",
        )
        read_only_fields = fields


class StockMovementCreateSerializer(serializers.Serializer):
    """Entrada, ajuste, perda ou devolução manual de estoque."""

    product_id = serializers.IntegerField()
    branch_id = serializers.IntegerField()
    type = serializers.ChoiceField(
        choices=[
            (StockMovementType.ENTRY, "Entrada"),
            (StockMovementType.ADJUSTMENT, "Ajuste"),
            (StockMovementType.LOSS, "Perda"),
            (StockMovementType.RETURN, "Devolução"),
        ]
    )
    quantity = serializers.IntegerField(
        help_text="Para ADJUSTMENT, informe o saldo final contado; nos demais, a quantidade."
    )
    unit_cost = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class SaleItemSerializer(serializers.ModelSerializer):
    product_detail = ProductSummarySerializer(source="product", read_only=True)

    class Meta:
        model = SaleItem
        fields = (
            "id",
            "product",
            "product_detail",
            "quantity",
            "unit_price",
            "total",
            "commission_percentage",
        )
        read_only_fields = ("id", "total", "commission_percentage")


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    client_name = serializers.CharField(
        source="client.user.full_name", read_only=True, default=None
    )
    barber_name = serializers.CharField(source="barber.display_name", read_only=True, default=None)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Sale
        fields = (
            "id",
            "uuid",
            "branch",
            "branch_name",
            "client",
            "client_name",
            "barber",
            "barber_name",
            "appointment",
            "status",
            "status_display",
            "subtotal",
            "discount_amount",
            "total",
            "notes",
            "items",
            "completed_at",
            "created_at",
        )
        read_only_fields = fields


class SaleItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )


class SaleCreateSerializer(serializers.Serializer):
    branch_id = serializers.IntegerField()
    client_id = serializers.IntegerField(required=False, allow_null=True)
    barber_id = serializers.IntegerField(required=False, allow_null=True)
    appointment_id = serializers.IntegerField(required=False, allow_null=True)
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=Decimal("0.00")
    )
    notes = serializers.CharField(required=False, allow_blank=True, max_length=300)
    items = SaleItemInputSerializer(many=True)
    payment_method = serializers.ChoiceField(
        choices=PaymentMethod.choices,
        required=False,
        help_text="Se informado, a venda já é concluída e paga.",
    )


class SaleCompleteSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices)


class SaleCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)
