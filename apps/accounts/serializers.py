"""Serializers de autenticação e usuário."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import password_validation
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import PasswordResetToken, User, UserRole
from apps.core.exceptions import BusinessError
from apps.core.utils import normalize_phone


class UserSerializer(serializers.ModelSerializer):
    """Representação pública do usuário autenticado."""

    name = serializers.CharField(source="full_name", read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "uuid",
            "name",
            "first_name",
            "last_name",
            "email",
            "phone",
            "role",
            "avatar_url",
            "is_active",
            "is_verified",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "uuid", "email", "role", "is_active", "is_verified")

    def get_avatar_url(self, obj: User) -> str | None:
        if not obj.avatar:
            return None
        request = self.context.get("request")
        url = obj.avatar.url
        return request.build_absolute_uri(url) if request else url


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    # `FileField` em vez do `ImageField` do DRF: o `ImageField` decodifica a
    # imagem antes da nossa validação, e um arquivo de 20 MB voltaria como
    # "envie uma imagem válida" em vez de falar do limite de tamanho.
    avatar = serializers.FileField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "phone", "avatar")

    def validate_avatar(self, arquivo):
        from apps.core.services.images import validate_avatar_file

        if arquivo is None:
            return None
        return validate_avatar_file(arquivo, serializers.ValidationError)

    def validate_phone(self, value: str) -> str:
        digits = normalize_phone(value)
        if digits and len(digits) not in (10, 11):
            raise serializers.ValidationError("Informe um telefone válido com DDD.")
        return digits


class SuaBarbeariaTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user: User) -> RefreshToken:
        token = super().get_token(user)
        token["role"] = user.role
        token["name"] = user.full_name
        token["uuid"] = str(user.uuid)
        return token

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # O e-mail é armazenado sempre em minúsculas; normalizamos a entrada
        # para que o login funcione independentemente de como foi digitado.
        field = self.username_field
        if isinstance(attrs.get(field), str):
            attrs[field] = attrs[field].strip().lower()

        data = super().validate(attrs)
        if not self.user.is_active:
            raise BusinessError(
                "Sua conta está inativa. Fale com a administração da Sua Barbearia.",
                code="ACCOUNT_INACTIVE",
                status_code=403,
            )
        data["user"] = UserSerializer(self.user, context=self.context).data
        return data


class RegisterSerializer(serializers.ModelSerializer):
    """Cadastro público — cria sempre um usuário com papel CLIENT."""

    password = serializers.CharField(
        write_only=True, min_length=8, style={"input_type": "password"}
    )
    password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})
    preferred_branch_id = serializers.IntegerField(required=False, allow_null=True)
    birth_date = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "email",
            "phone",
            "password",
            "password_confirm",
            "preferred_branch_id",
            "birth_date",
        )

    def validate_email(self, value: str) -> str:
        email = value.lower().strip()
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("Já existe uma conta com este e-mail.")
        return email

    def validate_phone(self, value: str) -> str:
        digits = normalize_phone(value)
        if digits and len(digits) not in (10, 11):
            raise serializers.ValidationError("Informe um telefone válido com DDD.")
        return digits

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["password"] != attrs.pop("password_confirm"):
            raise serializers.ValidationError({"password_confirm": "As senhas não conferem."})
        password_validation.validate_password(attrs["password"])
        return attrs

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> User:
        from apps.branches.models import Branch
        from apps.clients.models import Client

        preferred_branch_id = validated_data.pop("preferred_branch_id", None)
        birth_date = validated_data.pop("birth_date", None)
        password = validated_data.pop("password")

        user = User.objects.create_user(password=password, role=UserRole.CLIENT, **validated_data)

        branch = None
        if preferred_branch_id:
            branch = Branch.objects.filter(pk=preferred_branch_id, is_active=True).first()
            if branch is None:
                raise serializers.ValidationError({"preferred_branch_id": "Filial não encontrada."})

        Client.objects.create(user=user, preferred_branch=branch, birth_date=birth_date)
        return user


class LogoutSerializer(serializers.Serializer):
    """Invalida o refresh token informado."""

    refresh = serializers.CharField()

    def save(self, **kwargs: Any) -> None:
        try:
            RefreshToken(self.validated_data["refresh"]).blacklist()
        except Exception as exc:  # token inválido/expirado
            raise BusinessError(
                "Sessão inválida ou já encerrada.", code="INVALID_REFRESH_TOKEN"
            ) from exc


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_current_password(self, value: str) -> str:
        user: User = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Senha atual incorreta.")
        return value

    def validate_new_password(self, value: str) -> str:
        password_validation.validate_password(value, self.context["request"].user)
        return value

    def save(self, **kwargs: Any) -> User:
        user: User = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        return user


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.lower().strip()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        reset = (
            PasswordResetToken.objects.filter(token=attrs["token"]).select_related("user").first()
        )
        if reset is None or not reset.is_valid:
            raise BusinessError(
                "Link de redefinição inválido ou expirado. Solicite um novo.",
                code="INVALID_RESET_TOKEN",
            )
        password_validation.validate_password(attrs["new_password"], reset.user)
        attrs["reset"] = reset
        return attrs

    @transaction.atomic
    def save(self, **kwargs: Any) -> User:
        reset: PasswordResetToken = self.validated_data["reset"]
        user = reset.user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        reset.consume()
        return user


class UserAdminSerializer(serializers.ModelSerializer):
    """CRUD de usuários pelo OWNER (criação de barbeiros e outros perfis)."""

    password = serializers.CharField(
        write_only=True, required=False, min_length=8, style={"input_type": "password"}
    )
    name = serializers.CharField(source="full_name", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "uuid",
            "name",
            "first_name",
            "last_name",
            "email",
            "phone",
            "role",
            "avatar",
            "is_active",
            "is_verified",
            "password",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "uuid", "created_at", "updated_at")

    def validate_email(self, value: str) -> str:
        email = value.lower().strip()
        queryset = User.objects.filter(email=email)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("Já existe uma conta com este e-mail.")
        return email

    def create(self, validated_data: dict[str, Any]) -> User:
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "A senha é obrigatória."})
        password_validation.validate_password(password)
        return User.objects.create_user(password=password, **validated_data)

    def update(self, instance: User, validated_data: dict[str, Any]) -> User:
        password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            password_validation.validate_password(password, instance)
            instance.set_password(password)
        instance.save()
        return instance
