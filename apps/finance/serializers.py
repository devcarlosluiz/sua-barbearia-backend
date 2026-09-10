"""Serializers do financeiro."""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.finance.models import Commission, Transaction, TransactionCategory, TransactionType


class TransactionSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source="get_type_display", read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    barber_name = serializers.CharField(source="barber.display_name", read_only=True, default=None)
    created_by_name = serializers.CharField(
        source="created_by.full_name", read_only=True, default=None
    )

    class Meta:
        model = Transaction
        fields = (
            "id",
            "uuid",
            "branch",
            "branch_name",
            "type",
            "type_display",
            "category",
            "category_display",
            "description",
            "amount",
            "date",
            "notes",
            "payment",
            "appointment",
            "sale",
            "barber",
            "barber_name",
            "created_by",
            "created_by_name",
            "is_automatic",
            "created_at",
        )
        read_only_fields = ("id", "uuid", "created_by", "is_automatic", "created_at")

    def validate_amount(self, value: Decimal) -> Decimal:
        if value <= 0:
            raise serializers.ValidationError("O valor deve ser maior que zero.")
        return value


class CommissionSerializer(serializers.ModelSerializer):
    barber_name = serializers.CharField(source="barber.display_name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    service_name = serializers.CharField(
        source="appointment.service.name", read_only=True, default=None
    )
    client_name = serializers.CharField(
        source="appointment.client.user.full_name", read_only=True, default=None
    )

    class Meta:
        model = Commission
        fields = (
            "id",
            "uuid",
            "barber",
            "barber_name",
            "branch",
            "branch_name",
            "appointment",
            "sale_item",
            "service_name",
            "client_name",
            "base_amount",
            "percentage",
            "amount",
            "reference_date",
            "status",
            "status_display",
            "paid_at",
            "created_at",
        )
        read_only_fields = fields


class CommissionPayoutSerializer(serializers.Serializer):
    commission_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)


class CashFlowSummarySerializer(serializers.Serializer):
    """Resumo de caixa por período."""

    start_date = serializers.DateField()
    end_date = serializers.DateField()
    total_income = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_expense = serializers.DecimalField(max_digits=14, decimal_places=2)
    balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    by_category = serializers.ListField(child=serializers.DictField())
    by_payment_method = serializers.ListField(child=serializers.DictField())
    daily = serializers.ListField(child=serializers.DictField())


def category_choices() -> list[dict[str, str]]:
    """Categorias disponíveis, para popular selects no frontend."""
    return [{"value": value, "label": str(label)} for value, label in TransactionCategory.choices]


def type_choices() -> list[dict[str, str]]:
    return [{"value": value, "label": str(label)} for value, label in TransactionType.choices]
