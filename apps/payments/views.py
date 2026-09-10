"""Endpoints de pagamentos."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.exceptions import BusinessError
from apps.core.permissions import IsOwner, IsOwnerOrBarber
from apps.core.services import audit
from apps.payments.gateways import ChargeRequest, get_gateway
from apps.payments.models import Payment, PaymentStatus
from apps.payments.serializers import (
    PaymentCreateSerializer,
    PaymentRefundSerializer,
    PaymentSerializer,
)


@extend_schema_view(
    list=extend_schema(tags=["Pagamentos"], summary="Lista pagamentos"),
    retrieve=extend_schema(tags=["Pagamentos"], summary="Detalha um pagamento"),
)
class PaymentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Pagamentos. Barbeiro enxerga apenas os dos próprios atendimentos."""

    serializer_class = PaymentSerializer
    permission_classes = [IsOwnerOrBarber]
    filterset_fields = ("branch", "method", "status", "client")
    ordering_fields = ("paid_at", "created_at", "amount")
    ordering = ("-created_at",)

    def get_queryset(self) -> QuerySet[Payment]:
        queryset = Payment.objects.select_related("branch", "client__user", "appointment__service")
        user = self.request.user
        if user.is_barber:
            barber = getattr(user, "barber_profile", None)
            if barber is None:
                return queryset.none()
            return queryset.filter(appointment__barber=barber)
        return queryset

    @extend_schema(
        tags=["Pagamentos"],
        summary="Registra um pagamento",
        request=PaymentCreateSerializer,
        responses={201: PaymentSerializer},
    )
    @transaction.atomic
    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        gateway = get_gateway()
        result = gateway.charge(
            ChargeRequest(
                amount=data["amount"],
                method=data["method"],
                description="Pagamento Sua Barbearia",
                external_reference=str(data.get("appointment_id") or data.get("sale_id")),
            )
        )

        payment = Payment.objects.create(
            branch_id=data["branch_id"],
            appointment_id=data.get("appointment_id"),
            sale_id=data.get("sale_id"),
            client_id=data.get("client_id"),
            amount=data["amount"],
            discount_amount=data.get("discount_amount") or Decimal("0.00"),
            method=data["method"],
            status=data.get("status") or result.status,
            provider=gateway.provider,
            external_id=result.external_id,
            provider_payload=result.payload,
            paid_at=timezone.now() if result.status == PaymentStatus.PAID else None,
            notes=data.get("notes", ""),
            created_by=request.user,
        )
        audit.log_create(payment, user=request.user)
        return Response(PaymentSerializer(payment, context={"request": request}).data, status=201)

    @extend_schema(
        tags=["Pagamentos"],
        summary="Estorna um pagamento (OWNER)",
        request=PaymentRefundSerializer,
        responses={200: PaymentSerializer},
    )
    @action(detail=True, methods=["post"], permission_classes=[IsOwner])
    @transaction.atomic
    def refund(self, request: Request, pk: str | None = None) -> Response:
        payment = self.get_object()
        if payment.status != PaymentStatus.PAID:
            raise BusinessError(
                "Somente pagamentos quitados podem ser estornados.",
                code="PAYMENT_NOT_REFUNDABLE",
            )

        serializer = PaymentRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        gateway = get_gateway(payment.provider)
        result = gateway.refund(payment.external_id, payment.net_amount)

        old_data = audit.serialize_instance(payment)
        payment.status = PaymentStatus.REFUNDED
        payment.refunded_at = timezone.now()
        payment.provider_payload = {**payment.provider_payload, "refund": result.payload}
        payment.notes = (
            f"{payment.notes} | Estorno: {serializer.validated_data.get('reason', '')}".strip(" |")
        )
        payment.save(
            update_fields=["status", "refunded_at", "provider_payload", "notes", "updated_at"]
        )

        from apps.finance.models import TransactionCategory
        from apps.finance.services import record_expense

        record_expense(
            branch=payment.branch,
            amount=payment.net_amount,
            description=f"Estorno de pagamento {payment.uuid}",
            category=TransactionCategory.OTHER,
            created_by=request.user,
            is_automatic=True,
        )

        audit.log_update(payment, old_data, user=request.user)
        return Response(PaymentSerializer(payment, context={"request": request}).data)
