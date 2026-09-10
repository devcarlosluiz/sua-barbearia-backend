"""Serializers de barbeiros, jornada e ausências."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import password_validation
from django.db import transaction
from rest_framework import serializers

from apps.accounts.models import User, UserRole
from apps.barbers.models import (
    Barber,
    BarberService,
    SpecialWorkingHour,
    TimeOff,
    WorkingHour,
)
from apps.core.serializers import UniqueUserEmailMixin
from apps.core.utils import normalize_phone
from apps.services.serializers import ServiceSummarySerializer


class BarberUserWriteSerializer(UniqueUserEmailMixin, serializers.Serializer):
    """Dados do usuário do barbeiro (uso do OWNER), na criação e na edição."""

    first_name = serializers.CharField(max_length=80)
    last_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8, required=False)

    def validate_phone(self, value: str) -> str:
        return normalize_phone(value)


class BarberServiceSerializer(serializers.ModelSerializer):
    service_detail = ServiceSummarySerializer(source="service", read_only=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = BarberService
        fields = (
            "id",
            "service",
            "service_detail",
            "custom_price",
            "custom_duration_minutes",
            "price",
            "duration_minutes",
            "is_active",
        )


class WorkingHourSerializer(serializers.ModelSerializer):
    weekday_display = serializers.CharField(source="get_weekday_display", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = WorkingHour
        fields = (
            "id",
            "barber",
            "branch",
            "branch_name",
            "weekday",
            "weekday_display",
            "starts_at",
            "ends_at",
            "break_starts_at",
            "break_ends_at",
            "is_active",
        )
        read_only_fields = ("id",)

    def _incoming(self, attrs: dict[str, Any], field: str) -> Any:
        """Valor final do campo após esta escrita.

        Precisa distinguir "campo ausente do PATCH" de "campo enviado como
        nulo": usar `attrs.get(...) or instance.<field>` faz um PATCH que
        *limpa* o intervalo cair de volta no valor antigo, e a jornada é
        rejeitada por um intervalo que o cliente acabou de remover.
        """
        if field in attrs:
            return attrs[field]
        return getattr(self.instance, field, None)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        starts_at = self._incoming(attrs, "starts_at")
        ends_at = self._incoming(attrs, "ends_at")
        break_start = self._incoming(attrs, "break_starts_at")
        break_end = self._incoming(attrs, "break_ends_at")

        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError(
                {"ends_at": "O fim do expediente deve ser maior que o início."}
            )
        if bool(break_start) != bool(break_end):
            raise serializers.ValidationError(
                {"break_ends_at": "Informe início e fim do intervalo, ou nenhum dos dois."}
            )
        if break_start and break_end:
            if break_end <= break_start:
                raise serializers.ValidationError(
                    {"break_ends_at": "O fim do intervalo deve ser maior que o início."}
                )
            if break_start < starts_at or break_end > ends_at:
                raise serializers.ValidationError(
                    {"break_starts_at": "O intervalo precisa estar dentro do expediente."}
                )

        barber = attrs.get("barber") or getattr(self.instance, "barber", None)
        branch = attrs.get("branch") or getattr(self.instance, "branch", None)
        if barber and branch and not barber.branches.filter(pk=branch.pk).exists():
            raise serializers.ValidationError(
                {"branch": "O barbeiro não está vinculado a esta filial."}
            )
        return attrs


class SpecialWorkingHourSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = SpecialWorkingHour
        fields = (
            "id",
            "barber",
            "branch",
            "branch_name",
            "date",
            "starts_at",
            "ends_at",
            "break_starts_at",
            "break_ends_at",
            "note",
        )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if (
            attrs.get("ends_at")
            and attrs.get("starts_at")
            and attrs["ends_at"] <= attrs["starts_at"]
        ):
            raise serializers.ValidationError({"ends_at": "O fim deve ser maior que o início."})
        return attrs


class TimeOffSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source="get_type_display", read_only=True)
    barber_name = serializers.CharField(source="barber.display_name", read_only=True)

    class Meta:
        model = TimeOff
        fields = (
            "id",
            "uuid",
            "barber",
            "barber_name",
            "branch",
            "type",
            "type_display",
            "starts_at",
            "ends_at",
            "reason",
            "created_at",
        )
        read_only_fields = ("id", "uuid", "created_at")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        starts_at = attrs.get("starts_at") or getattr(self.instance, "starts_at", None)
        ends_at = attrs.get("ends_at") or getattr(self.instance, "ends_at", None)
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError(
                {"ends_at": "O fim da ausência deve ser maior que o início."}
            )
        return attrs


class BarberListSerializer(serializers.ModelSerializer):
    """Payload enxuto para seleção de barbeiro e listagens."""

    name = serializers.CharField(source="display_name", read_only=True)
    avatar_url = serializers.SerializerMethodField()
    branch_ids = serializers.SerializerMethodField()

    class Meta:
        model = Barber
        fields = (
            "id",
            "uuid",
            "name",
            "nickname",
            "avatar_url",
            "specialties",
            # Campo local do próprio Barber: exibido no card da listagem e
            # sem custo de query adicional.
            "commission_percentage",
            "rating",
            "reviews_count",
            "branch_ids",
            "is_active",
        )

    def get_avatar_url(self, obj: Barber) -> str | None:
        avatar = obj.user.avatar
        if not avatar:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(avatar.url) if request else avatar.url

    def get_branch_ids(self, obj: Barber) -> list[int]:
        return [branch.id for branch in obj.branches.all()]


class BarberSerializer(serializers.ModelSerializer):
    """Payload completo do barbeiro, com criação/atualização do usuário."""

    user = BarberUserWriteSerializer(write_only=True, required=False)
    user_id = serializers.PrimaryKeyRelatedField(source="user", read_only=True)
    name = serializers.CharField(source="display_name", read_only=True)
    # `name` é o nome de exibição (apelido, quando houver). Os campos reais são
    # expostos separadamente porque o formulário de edição precisa deles — usar
    # `name` para preencher nome/sobrenome sobrescreveria o cadastro do usuário.
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    avatar_url = serializers.SerializerMethodField()
    is_user_active = serializers.BooleanField(source="user.is_active", read_only=True)
    barber_services = BarberServiceSerializer(many=True, read_only=True)
    working_hours = WorkingHourSerializer(many=True, read_only=True)
    service_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False
    )

    class Meta:
        model = Barber
        fields = (
            "id",
            "uuid",
            "user",
            "user_id",
            "name",
            "first_name",
            "last_name",
            "nickname",
            "email",
            "phone",
            "avatar_url",
            "bio",
            "specialties",
            "commission_percentage",
            "rating",
            "reviews_count",
            "hired_at",
            "is_active",
            "is_user_active",
            "branches",
            "barber_services",
            "working_hours",
            "service_ids",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "uuid", "rating", "reviews_count", "created_at", "updated_at")

    def get_avatar_url(self, obj: Barber) -> str | None:
        avatar = obj.user.avatar
        if not avatar:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(avatar.url) if request else avatar.url

    def validate_specialties(self, value: Any) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise serializers.ValidationError("Informe uma lista de textos.")
        return value

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> Barber:
        from django.conf import settings

        user_data = validated_data.pop("user", None)
        if user_data is None:
            raise serializers.ValidationError({"user": "Informe os dados do usuário do barbeiro."})
        branches = validated_data.pop("branches", [])
        service_ids = validated_data.pop("service_ids", [])

        password = user_data.pop("password", None) or settings.SEED_DEFAULT_PASSWORD
        password_validation.validate_password(password)
        user = User.objects.create_user(
            password=password, role=UserRole.BARBER, is_verified=True, **user_data
        )

        barber = Barber.objects.create(user=user, **validated_data)
        barber.branches.set(branches)
        self._sync_services(barber, service_ids)
        return barber

    @transaction.atomic
    def update(self, instance: Barber, validated_data: dict[str, Any]) -> Barber:
        user_data = validated_data.pop("user", None)
        branches = validated_data.pop("branches", None)
        service_ids = validated_data.pop("service_ids", None)

        if user_data:
            user_data.pop("password", None)
            user_data.pop("email", None)  # e-mail é alterado pelo endpoint de usuários
            for field, value in user_data.items():
                setattr(instance.user, field, value)
            instance.user.save()

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if branches is not None:
            instance.branches.set(branches)
        if service_ids is not None:
            self._sync_services(instance, service_ids)
        return instance

    @staticmethod
    def _sync_services(barber: Barber, service_ids: list[int]) -> None:
        BarberService.objects.filter(barber=barber).exclude(service_id__in=service_ids).update(
            is_active=False
        )
        for service_id in service_ids:
            BarberService.objects.update_or_create(
                barber=barber, service_id=service_id, defaults={"is_active": True}
            )
