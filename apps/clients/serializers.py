"""Serializers de clientes."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import password_validation
from django.db import transaction
from rest_framework import serializers

from apps.accounts.models import User, UserRole
from apps.clients.models import Client
from apps.core.serializers import UniqueUserEmailMixin
from apps.core.utils import is_valid_cpf, normalize_phone, only_digits


class ClientUserWriteSerializer(UniqueUserEmailMixin, serializers.Serializer):
    """Dados do usuário do cliente (OWNER/BARBER), na criação e na edição."""

    first_name = serializers.CharField(max_length=80)
    last_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8, required=False)

    def validate_phone(self, value: str) -> str:
        return normalize_phone(value)


class ClientListSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    preferred_branch_name = serializers.CharField(
        source="preferred_branch.name", read_only=True, default=None
    )

    class Meta:
        model = Client
        fields = (
            "id",
            "uuid",
            "name",
            "email",
            "phone",
            "preferred_branch",
            "preferred_branch_name",
            "loyalty_points",
            "total_visits",
            "total_spent",
            "last_visit_at",
        )


class ClientSerializer(serializers.ModelSerializer):
    """Payload completo do cliente."""

    user = ClientUserWriteSerializer(write_only=True, required=False)
    name = serializers.CharField(source="user.full_name", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    avatar_url = serializers.SerializerMethodField()
    preferred_branch_name = serializers.CharField(
        source="preferred_branch.name", read_only=True, default=None
    )
    preferred_barber_name = serializers.CharField(
        source="preferred_barber.display_name", read_only=True, default=None
    )
    favorite_service_name = serializers.CharField(
        source="favorite_service.name", read_only=True, default=None
    )
    average_ticket = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Client
        fields = (
            "id",
            "uuid",
            "user",
            "name",
            "first_name",
            "last_name",
            "email",
            "phone",
            "avatar_url",
            "birth_date",
            "cpf",
            "preferred_branch",
            "preferred_branch_name",
            "preferred_barber",
            "preferred_barber_name",
            "notes",
            "accepts_marketing",
            "loyalty_points",
            "total_visits",
            "total_spent",
            "average_ticket",
            "last_visit_at",
            "favorite_service",
            "favorite_service_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "uuid",
            "loyalty_points",
            "total_visits",
            "total_spent",
            "last_visit_at",
            "favorite_service",
            "created_at",
            "updated_at",
        )

    def get_avatar_url(self, obj: Client) -> str | None:
        avatar = obj.user.avatar
        if not avatar:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(avatar.url) if request else avatar.url

    def validate_cpf(self, value: str) -> str:
        digits = only_digits(value)
        if digits and not is_valid_cpf(digits):
            raise serializers.ValidationError("CPF inválido.")
        return digits

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> Client:
        user_data = validated_data.pop("user", None)
        if user_data is None:
            raise serializers.ValidationError({"user": "Informe os dados do cliente."})
        password = user_data.pop("password", None) or settings.SEED_DEFAULT_PASSWORD
        password_validation.validate_password(password)
        user = User.objects.create_user(password=password, role=UserRole.CLIENT, **user_data)
        return Client.objects.create(user=user, **validated_data)

    @transaction.atomic
    def update(self, instance: Client, validated_data: dict[str, Any]) -> Client:
        user_data = validated_data.pop("user", None)
        if user_data:
            user_data.pop("password", None)
            user_data.pop("email", None)
            for field, value in user_data.items():
                setattr(instance.user, field, value)
            instance.user.save()
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class ClientSelfUpdateSerializer(serializers.ModelSerializer):
    """O cliente só pode ajustar as próprias preferências."""

    class Meta:
        model = Client
        fields = (
            "birth_date",
            "cpf",
            "preferred_branch",
            "preferred_barber",
            "accepts_marketing",
        )

    def validate_cpf(self, value: str) -> str:
        digits = only_digits(value)
        if digits and not is_valid_cpf(digits):
            raise serializers.ValidationError("CPF inválido.")
        return digits
