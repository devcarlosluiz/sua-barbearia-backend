"""Serializers de agendamentos."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.appointments.models import Appointment, AppointmentStatus, AppointmentStatusHistory
from apps.payments.models import PaymentMethod


class AppointmentStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(
        source="changed_by.full_name", read_only=True, default=None
    )
    from_status_display = serializers.CharField(source="get_from_status_display", read_only=True)
    to_status_display = serializers.CharField(source="get_to_status_display", read_only=True)

    class Meta:
        model = AppointmentStatusHistory
        fields = (
            "id",
            "from_status",
            "from_status_display",
            "to_status",
            "to_status_display",
            "changed_by_name",
            "reason",
            "metadata",
            "created_at",
        )


class AppointmentListSerializer(serializers.ModelSerializer):
    """Payload usado em agendas e listagens."""

    client_name = serializers.CharField(source="client.user.full_name", read_only=True)
    client_phone = serializers.CharField(source="client.user.phone", read_only=True)
    barber_name = serializers.CharField(source="barber.display_name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)
    # Vive aqui, e não só no detalhe: a lista do cliente é justamente onde ficam
    # os botões de cancelar e excluir. Sem este campo o app recebia `false` por
    # omissão e escondia as duas ações.
    can_be_cancelled_by_client = serializers.SerializerMethodField()
    # O plano mensal precisa aparecer *antes* da finalização: sem este campo a
    # agenda do barbeiro mostrava o preço cheio e mandava cobrar um cliente que
    # já pagou a mensalidade. `null` quando não há nada a dizer.
    plan_coverage = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = (
            "id",
            "uuid",
            "client",
            "client_name",
            "client_phone",
            "barber",
            "barber_name",
            "branch",
            "branch_name",
            "service",
            "service_name",
            "date",
            "start_time",
            "end_time",
            "duration_minutes",
            "price",
            "status",
            "status_display",
            "notes",
            "can_be_cancelled_by_client",
            "plan_coverage",
            "created_at",
        )

    def get_can_be_cancelled_by_client(self, obj: Appointment) -> bool:
        """O cliente ainda está dentro do prazo de antecedência da filial?"""
        from datetime import timedelta

        from django.utils import timezone

        if obj.is_final:
            return False
        deadline = obj.start_datetime - timedelta(hours=obj.branch.cancellation_limit_hours)
        return timezone.now() <= deadline

    def get_plan_coverage(self, obj: Appointment) -> dict[str, Any] | None:
        return self._coverage_map().get(obj.pk)

    def _coverage_map(self) -> dict[int, Any]:
        """Resolve a cobertura da lista inteira de uma vez.

        O campo é por item, mas a consulta não pode ser: uma agenda de trinta
        linhas faria noventa consultas. O mapa é calculado na primeira linha,
        a partir do `instance` da raiz, e reaproveitado pelas demais.
        """
        root = self.parent if isinstance(self.parent, serializers.ListSerializer) else self
        cached = getattr(root, "_plan_coverage_cache", None)
        if cached is None:
            from apps.plans.services import appointment_coverage_map

            instance = root.instance
            items = [instance] if isinstance(instance, Appointment) else list(instance or [])
            cached = appointment_coverage_map(items)
            root._plan_coverage_cache = cached
        return cached


class AppointmentDetailSerializer(AppointmentListSerializer):
    """Payload completo, com histórico e dados do ciclo de atendimento."""

    status_history = AppointmentStatusHistorySerializer(many=True, read_only=True)
    cancelled_by_name = serializers.CharField(
        source="cancelled_by.full_name", read_only=True, default=None
    )
    has_review = serializers.SerializerMethodField()
    commission_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta(AppointmentListSerializer.Meta):
        fields = (
            *AppointmentListSerializer.Meta.fields,
            "internal_notes",
            "confirmed_at",
            "arrived_at",
            "started_at",
            "completed_at",
            "cancelled_at",
            "cancelled_by_role",
            "cancelled_by_name",
            "cancellation_reason",
            "rescheduled_from",
            "commission_amount",
            "has_review",
            "status_history",
            "updated_at",
        )

    def get_has_review(self, obj: Appointment) -> bool:
        return hasattr(obj, "review")


class AppointmentCreateSerializer(serializers.Serializer):
    """Entrada da criação de agendamento (a regra vive no service de booking)."""

    branch_id = serializers.IntegerField()
    barber_id = serializers.IntegerField()
    service_id = serializers.IntegerField()
    client_id = serializers.IntegerField(
        required=False,
        help_text="Somente OWNER/BARBER. O cliente autenticado sempre agenda para si.",
    )
    date = serializers.DateField()
    start_time = serializers.TimeField()
    notes = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class AppointmentRescheduleSerializer(serializers.Serializer):
    date = serializers.DateField()
    start_time = serializers.TimeField()
    barber_id = serializers.IntegerField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class AppointmentCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class AppointmentStatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=AppointmentStatus.choices)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class AppointmentCompleteSerializer(serializers.Serializer):
    """Finalização do atendimento com registro de pagamento."""

    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices)
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, min_value=Decimal("0.00")
    )
    discount_amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, default=Decimal("0.00")
    )
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)


class AvailableSlotsQuerySerializer(serializers.Serializer):
    branch_id = serializers.IntegerField()
    barber_id = serializers.IntegerField()
    service_id = serializers.IntegerField()
    date = serializers.DateField()


class AvailableSlotsResponseSerializer(serializers.Serializer):
    date = serializers.DateField()
    branch_id = serializers.IntegerField()
    barber_id = serializers.IntegerField()
    service_id = serializers.IntegerField()
    duration_minutes = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    slots = serializers.ListField(child=serializers.CharField())


class AgendaQuerySerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    branch_id = serializers.IntegerField(required=False)
    barber_id = serializers.IntegerField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return attrs
