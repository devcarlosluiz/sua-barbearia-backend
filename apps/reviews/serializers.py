"""Serializers de avaliações."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.appointments.models import Appointment, AppointmentStatus
from apps.core.exceptions import BusinessError
from apps.reviews.models import Review


class ReviewSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.user.full_name", read_only=True)
    barber_name = serializers.CharField(source="barber.display_name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    service_name = serializers.CharField(source="appointment.service.name", read_only=True)
    appointment_date = serializers.DateField(source="appointment.date", read_only=True)

    class Meta:
        model = Review
        fields = (
            "id",
            "uuid",
            "appointment",
            "appointment_date",
            "client",
            "client_name",
            "barber",
            "barber_name",
            "branch",
            "branch_name",
            "service_name",
            "rating",
            "comment",
            "is_published",
            "reply",
            "replied_at",
            "created_at",
        )
        read_only_fields = (
            "id",
            "uuid",
            "client",
            "barber",
            "branch",
            "reply",
            "replied_at",
            "created_at",
        )


class ReviewCreateSerializer(serializers.ModelSerializer):
    """Criação da avaliação pelo cliente do atendimento."""

    class Meta:
        model = Review
        fields = ("appointment", "rating", "comment")
        # Desativa o UniqueValidator padrão para que a checagem de duplicidade
        # abaixo responda com o código de negócio REVIEW_ALREADY_EXISTS.
        extra_kwargs = {"appointment": {"validators": []}}

    def validate_appointment(self, appointment: Appointment) -> Appointment:
        request = self.context["request"]
        client = getattr(request.user, "client_profile", None)

        if client is None or appointment.client_id != client.pk:
            raise BusinessError(
                "Você só pode avaliar os seus próprios atendimentos.",
                code="PERMISSION_DENIED",
                status_code=403,
            )
        if appointment.status != AppointmentStatus.COMPLETED:
            raise BusinessError(
                "Só é possível avaliar atendimentos concluídos.",
                code="APPOINTMENT_NOT_COMPLETED",
            )
        if Review.objects.filter(appointment=appointment).exists():
            raise BusinessError("Este atendimento já foi avaliado.", code="REVIEW_ALREADY_EXISTS")
        return appointment

    def create(self, validated_data: dict[str, Any]) -> Review:
        appointment: Appointment = validated_data["appointment"]
        return Review.objects.create(
            appointment=appointment,
            client=appointment.client,
            barber=appointment.barber,
            branch=appointment.branch,
            rating=validated_data["rating"],
            comment=validated_data.get("comment", ""),
        )


class ReviewReplySerializer(serializers.Serializer):
    reply = serializers.CharField(max_length=1000)
