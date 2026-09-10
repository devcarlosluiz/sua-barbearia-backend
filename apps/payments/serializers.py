"""Serializers de pagamentos."""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.payments.models import Payment, PaymentMethod, PaymentStatus


class PaymentSerializer(serializers.ModelSerializer):
    method_display = serializers.CharField(source="get_method_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    client_name = serializers.CharField(
        source="client.user.full_name", read_only=True, default=None
    )
    net_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Payment
        fields = (
            "id",
            "uuid",
            "branch",
            "branch_name",
            "appointment",
            "sale",
            "client",
            "client_name",
            "amount",
            "discount_amount",
            "net_amount",
            "method",
            "method_display",
            "status",
            "status_display",
            "provider",
            "external_id",
            "paid_at",
            "refunded_at",
            "notes",
            "created_at",
        )
        read_only_fields = (
            "id",
            "uuid",
            "provider",
            "external_id",
            "paid_at",
            "refunded_at",
            "created_at",
        )


class PaymentCreateSerializer(serializers.Serializer):
    """Registro de um pagamento avulso vinculado a atendimento ou venda."""

    branch_id = serializers.IntegerField()
    appointment_id = serializers.IntegerField(required=False, allow_null=True)
    sale_id = serializers.IntegerField(required=False, allow_null=True)
    client_id = serializers.IntegerField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    discount_amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, default=Decimal("0.00")
    )
    method = serializers.ChoiceField(choices=PaymentMethod.choices)
    status = serializers.ChoiceField(
        choices=PaymentStatus.choices, required=False, default=PaymentStatus.PAID
    )
    notes = serializers.CharField(required=False, allow_blank=True, max_length=300)

    def validate(self, attrs: dict) -> dict:
        if not attrs.get("appointment_id") and not attrs.get("sale_id"):
            raise serializers.ValidationError(
                "Informe o atendimento ou a venda referente ao pagamento."
            )
        if attrs.get("discount_amount", Decimal("0")) > attrs["amount"]:
            raise serializers.ValidationError(
                {"discount_amount": "O desconto não pode ser maior que o valor."}
            )
        return attrs


class PaymentRefundSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)
