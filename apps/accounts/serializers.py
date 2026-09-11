"""Serializers de autenticação e usuário."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.google import GoogleIdentity, verify_google_id_token
from apps.accounts.models import PasswordResetToken, User, UserRole
from apps.core.exceptions import BusinessError
from apps.core.utils import normalize_phone


class UserSerializer(serializers.ModelSerializer):
    """Representação pública do usuário autenticado."""

    name = serializers.CharField(source="full_name", read_only=True)
    avatar_url = serializers.SerializerMethodField()
    # Quem entrou pelo Google não definiu senha. O app usa estes dois campos
    # para escolher entre "alterar senha" e "criar senha" na tela de conta.
    has_google_account = serializers.BooleanField(read_only=True)
    has_password = serializers.SerializerMethodField()

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
            "has_google_account",
            "has_password",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "uuid", "email", "role", "is_active", "is_verified")

    def get_has_password(self, obj: User) -> bool:
        return obj.has_usable_password()

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


def resolve_preferred_branch(branch_id: int | None):
    """Filial preferida escolhida no cadastro. `None` quando não informada."""
    from apps.branches.models import Branch

    if not branch_id:
        return None
    branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
    if branch is None:
        raise serializers.ValidationError({"preferred_branch_id": "Filial não encontrada."})
    return branch


class RegisterSerializer(serializers.ModelSerializer):
    """Cadastro público — cria sempre um usuário com papel CLIENT.

    `birth_date` é opcional de propósito: pedir a data de nascimento na
    primeira tela derruba cadastro. O cliente informa depois, quando quiser,
    em `PATCH /clients/me/`.
    """

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
        from apps.clients.models import Client

        preferred_branch_id = validated_data.pop("preferred_branch_id", None)
        birth_date = validated_data.pop("birth_date", None)
        password = validated_data.pop("password")

        user = User.objects.create_user(password=password, role=UserRole.CLIENT, **validated_data)
        branch = resolve_preferred_branch(preferred_branch_id)

        Client.objects.create(user=user, preferred_branch=branch, birth_date=birth_date)
        return user


class GoogleAuthSerializer(serializers.Serializer):
    """Entrada com a conta Google — o mesmo endpoint cadastra e faz login.

    O app manda o `id_token` devolvido pelo Sign in with Google. Se ainda não
    houver conta, criamos uma com papel CLIENT: sem senha e sem data de
    nascimento, porque o Google já provou quem é a pessoa e tudo o que falta no
    perfil pode ser preenchido depois.

    Expõe `created` depois do `save()` para a view decidir entre 200 e 201.
    """

    id_token = serializers.CharField(write_only=True)
    preferred_branch_id = serializers.IntegerField(required=False, allow_null=True)

    created: bool = False

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> User:
        identity = verify_google_id_token(validated_data["id_token"])
        branch = resolve_preferred_branch(validated_data.get("preferred_branch_id"))

        user = self._find_user(identity)
        self.created = False
        if user is None:
            user, self.created = self._create_user(identity)

        if not user.is_active:
            raise BusinessError(
                "Sua conta está inativa. Fale com a administração da Sua Barbearia.",
                code="ACCOUNT_INACTIVE",
                status_code=403,
            )

        # Barbeiro e proprietário também podem entrar pelo Google, mas só o
        # cliente tem perfil criado aqui — os outros são cadastrados pela
        # administração, com a filial e a comissão que lhes cabem.
        if user.is_client:
            self._ensure_client_profile(user, branch)
        return user

    def _create_user(self, identity: GoogleIdentity) -> tuple[User, bool]:
        """Cria a conta do cliente, tolerando dois cliques simultâneos.

        O savepoint próprio existe por causa da corrida: dois toques no botão
        "Entrar com o Google" chegam quase juntos e os dois passam pela busca
        sem achar ninguém. O segundo esbarra no `unique` e, em vez de devolver
        erro, reaproveita a conta que o primeiro acabou de criar — sem o
        savepoint, o erro do banco derrubaria a transação inteira.
        """
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=identity.email,
                    password=None,  # conta sem senha: entra só pelo Google
                    first_name=identity.first_name,
                    last_name=identity.last_name,
                    role=UserRole.CLIENT,
                    google_id=identity.sub,
                    is_verified=True,
                )
            return user, True
        except (IntegrityError, DjangoValidationError):
            existente = self._find_user(identity)
            if existente is None:
                raise
            return existente, False

    def _find_user(self, identity: GoogleIdentity) -> User | None:
        user = User.objects.filter(google_id=identity.sub).first()
        if user is not None:
            return user

        # Conta criada antes por e-mail e senha: o Google confirmou que o
        # e-mail é desta pessoa, então vinculamos em vez de recusar o login
        # com "já existe uma conta com este e-mail".
        user = User.objects.filter(email=identity.email).first()
        if user is None:
            return None

        campos = ["google_id", "updated_at"]
        user.google_id = identity.sub
        if not user.is_verified:
            user.is_verified = True
            campos.append("is_verified")
        user.save(update_fields=campos)
        return user

    def _ensure_client_profile(self, user: User, branch: Any | None) -> None:
        from apps.clients.models import Client

        profile, _ = Client.objects.get_or_create(user=user)
        # A filial só é gravada no primeiro acesso: depois disso, quem manda é
        # a preferência que o cliente ajustou no app.
        if branch is not None and profile.preferred_branch_id is None:
            profile.preferred_branch = branch
            profile.save(update_fields=["preferred_branch", "updated_at"])


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
