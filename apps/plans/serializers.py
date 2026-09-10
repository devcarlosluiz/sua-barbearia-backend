"""Serializers de planos e assinaturas."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import serializers

from apps.branches.models import Branch
from apps.plans.models import (
    BillingType,
    Plan,
    PlanService,
    Subscription,
    SubscriptionInvoice,
    SubscriptionUsage,
)
from apps.plans.services import quota_summary


class PlanServiceSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)
    service_price = serializers.DecimalField(
        source="service.price", max_digits=10, decimal_places=2, read_only=True
    )
    service_duration_minutes = serializers.IntegerField(
        source="service.duration_minutes", read_only=True
    )
    is_unlimited = serializers.BooleanField(read_only=True)

    class Meta:
        model = PlanService
        fields = (
            "id",
            "service",
            "service_name",
            "service_price",
            "service_duration_minutes",
            "monthly_quota",
            "is_unlimited",
        )


class PlanServiceWriteSerializer(serializers.Serializer):
    """Item da composição do plano, enviado pelo proprietário."""

    service = serializers.IntegerField()
    monthly_quota = serializers.IntegerField(min_value=0, default=1)


class PlanListSerializer(serializers.ModelSerializer):
    """Payload de vitrine: é o que o cliente vê antes de assinar."""

    plan_services = PlanServiceSerializer(many=True, read_only=True)
    branch_ids = serializers.SerializerMethodField()
    subscribers_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Plan
        fields = (
            "id",
            "uuid",
            "name",
            "description",
            "price",
            "overage_discount_percentage",
            "is_active",
            "is_public",
            "branch_ids",
            "plan_services",
            "subscribers_count",
        )

    def get_branch_ids(self, obj: Plan) -> list[int]:
        return [branch.id for branch in obj.branches.all()]


class PlanSerializer(PlanListSerializer):
    """Payload completo, com escrita da composição de serviços."""

    service_items = PlanServiceWriteSerializer(many=True, write_only=True, required=False)

    class Meta(PlanListSerializer.Meta):
        fields = (
            *PlanListSerializer.Meta.fields,
            "pays_barber_commission",
            "branches",
            "service_items",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "uuid", "created_at", "updated_at")

    def validate_service_items(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        for item in value:
            service_id = item["service"]
            if service_id in seen:
                raise serializers.ValidationError("Cada serviço pode aparecer uma única vez.")
            seen.add(service_id)
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # Um plano sem serviço não entrega nada; barrar na criação evita uma
        # vitrine com plano vazio.
        items = attrs.get("service_items")
        if self.instance is None and not items:
            raise serializers.ValidationError(
                {"service_items": "Inclua ao menos um serviço no plano."}
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> Plan:
        items = validated_data.pop("service_items", [])
        branches = validated_data.pop("branches", [])
        plan = Plan.objects.create(**validated_data)
        plan.branches.set(branches)
        self._sync_services(plan, items)
        return plan

    @transaction.atomic
    def update(self, instance: Plan, validated_data: dict[str, Any]) -> Plan:
        items = validated_data.pop("service_items", None)
        branches = validated_data.pop("branches", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if branches is not None:
            instance.branches.set(branches)
        if items is not None:
            self._sync_services(instance, items)
        return instance

    @staticmethod
    def _sync_services(plan: Plan, items: list[dict[str, Any]]) -> None:
        keep = [item["service"] for item in items]
        PlanService.objects.filter(plan=plan).exclude(service_id__in=keep).delete()
        for item in items:
            PlanService.objects.update_or_create(
                plan=plan,
                service_id=item["service"],
                defaults={"monthly_quota": item.get("monthly_quota", 1)},
            )


class SubscriptionInvoiceSerializer(serializers.ModelSerializer):
    """Fatura de um ciclo.

    Expõe o QR do PIX e a URL de checkout — ambos são dados públicos feitos
    para o cliente ver. `provider_payload` fica fora de propósito: é retorno
    bruto do provedor e não deve vazar para o app.
    """

    plan_name = serializers.CharField(source="subscription.plan.name", read_only=True)
    client_name = serializers.CharField(source="subscription.client.full_name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = SubscriptionInvoice
        fields = (
            "id",
            "uuid",
            "subscription",
            "plan_name",
            "client_name",
            "period_start",
            "period_end",
            "amount",
            "method",
            "status",
            "status_display",
            "due_date",
            "pix_qr_code",
            "pix_qr_code_base64",
            "checkout_url",
            "expires_at",
            "paid_at",
            "created_at",
        )
        read_only_fields = fields


class SubscriptionQuotaSerializer(serializers.Serializer):
    """Cota por serviço no ciclo atual."""

    service = serializers.IntegerField()
    service_name = serializers.CharField()
    monthly_quota = serializers.IntegerField()
    is_unlimited = serializers.BooleanField()
    used = serializers.IntegerField()
    remaining = serializers.IntegerField(allow_null=True)


class SubscriptionSerializer(serializers.ModelSerializer):
    plan_detail = PlanListSerializer(source="plan", read_only=True)
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    client_phone = serializers.CharField(source="client.user.phone", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    billing_type_display = serializers.CharField(source="get_billing_type_display", read_only=True)
    grants_benefit = serializers.BooleanField(read_only=True)
    quotas = serializers.SerializerMethodField()
    open_invoice = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = (
            "id",
            "uuid",
            "client",
            "client_name",
            "client_phone",
            "plan",
            "plan_detail",
            "branch",
            "branch_name",
            "status",
            "status_display",
            "billing_type",
            "billing_type_display",
            "price",
            "current_period_start",
            "current_period_end",
            "grants_benefit",
            "cancel_at_period_end",
            "started_at",
            "cancelled_at",
            "quotas",
            "open_invoice",
            "created_at",
        )
        read_only_fields = fields

    def get_quotas(self, obj: Subscription) -> list[dict[str, Any]]:
        if obj.current_period_start is None:
            return []
        return quota_summary(obj)

    def get_open_invoice(self, obj: Subscription) -> dict[str, Any] | None:
        """A cobrança que o cliente precisa pagar agora, se houver."""
        invoice = obj.invoices.filter(status="PENDING").order_by("-period_start").first()
        if invoice is None:
            return None
        return SubscriptionInvoiceSerializer(invoice, context=self.context).data


class SubscribeSerializer(serializers.Serializer):
    """Entrada de `POST /subscriptions/` — o cliente assinando um plano."""

    plan = serializers.PrimaryKeyRelatedField(queryset=Plan.objects.all())
    billing_type = serializers.ChoiceField(choices=BillingType.choices)
    branch = serializers.PrimaryKeyRelatedField(
        queryset=Branch.objects.filter(is_active=True), required=False, allow_null=True
    )

    def validate_plan(self, value: Plan) -> Plan:
        # Um plano oculto ou inativo não pode ser assinado nem por link direto.
        if not value.is_available:
            raise serializers.ValidationError("Este plano não está disponível.")
        return value


class ConfirmInvoiceSerializer(serializers.Serializer):
    """Confirmação manual de fatura pelo caixa (uso do proprietário)."""

    notes = serializers.CharField(required=False, allow_blank=True, max_length=300)


class CancelSubscriptionSerializer(serializers.Serializer):
    immediate = serializers.BooleanField(
        default=False,
        help_text="Encerra na hora, sem manter o benefício até o fim do ciclo pago.",
    )


class SubscriptionUsageSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)
    client_name = serializers.CharField(source="subscription.client.full_name", read_only=True)

    class Meta:
        model = SubscriptionUsage
        fields = (
            "id",
            "subscription",
            "appointment",
            "service",
            "service_name",
            "client_name",
            "period_start",
            "covered_amount",
            "used_at",
        )
        read_only_fields = fields
