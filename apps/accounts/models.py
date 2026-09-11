"""Modelo de usuário customizado da Sua Barbearia."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.core.utils import normalize_phone


class UserRole(models.TextChoices):
    OWNER = "OWNER", _("Proprietário")
    BARBER = "BARBER", _("Barbeiro")
    CLIENT = "CLIENT", _("Cliente")


class UserManager(BaseUserManager):
    """Manager que usa e-mail como identificador de login."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra: Any) -> User:
        if not email:
            raise ValueError("O e-mail é obrigatório.")
        email = self.normalize_email(email).lower()
        extra.setdefault("role", UserRole.CLIENT)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", UserRole.OWNER)
        extra.setdefault("is_verified", True)
        if extra.get("is_staff") is not True:
            raise ValueError("Superusuário precisa ter is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superusuário precisa ter is_superuser=True.")
        return self._create_user(email, password, **extra)


def user_avatar_path(instance: User, filename: str) -> str:
    return f"avatars/users/{instance.uuid}/{filename}"


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    """Usuário único do sistema, diferenciado pelo campo `role`."""

    first_name = models.CharField(_("nome"), max_length=80)
    last_name = models.CharField(_("sobrenome"), max_length=120, blank=True)
    email = models.EmailField(_("e-mail"), unique=True, db_index=True)
    phone = models.CharField(_("telefone"), max_length=20, blank=True, db_index=True)
    role = models.CharField(
        _("perfil"),
        max_length=10,
        choices=UserRole.choices,
        default=UserRole.CLIENT,
        db_index=True,
    )
    avatar = models.ImageField(_("avatar"), upload_to=user_avatar_path, blank=True, null=True)
    # `sub` do ID token do Google — o identificador estável da conta Google.
    # Guardamos ele, e não o e-mail, porque o e-mail do Google pode mudar.
    # `null` (e não string vazia) para que o `unique` não colida entre as
    # contas que entram por e-mail e senha.
    google_id = models.CharField(
        _("ID da conta Google"), max_length=64, unique=True, null=True, blank=True
    )
    is_active = models.BooleanField(_("ativo"), default=True)
    is_verified = models.BooleanField(_("verificado"), default=False)
    is_staff = models.BooleanField(_("acesso ao admin"), default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name"]

    class Meta:
        verbose_name = _("usuário")
        verbose_name_plural = _("usuários")
        ordering = ("first_name", "last_name")
        indexes = [
            models.Index(fields=["role", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} <{self.email}>"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = self.email.lower().strip()
        self.phone = normalize_phone(self.phone)
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_full_name(self) -> str:
        return self.full_name

    def get_short_name(self) -> str:
        return self.first_name

    @property
    def is_owner(self) -> bool:
        return self.role == UserRole.OWNER

    @property
    def is_barber(self) -> bool:
        return self.role == UserRole.BARBER

    @property
    def is_client(self) -> bool:
        return self.role == UserRole.CLIENT

    @property
    def has_google_account(self) -> bool:
        return bool(self.google_id)


class PasswordResetToken(models.Model):
    """Token de uso único para redefinição de senha."""

    TTL_HOURS = 2

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("token de redefinição de senha")
        verbose_name_plural = _("tokens de redefinição de senha")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"reset:{self.user_id}"

    @classmethod
    def issue(cls, user: User) -> PasswordResetToken:
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        return cls.objects.create(
            user=user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(hours=cls.TTL_HOURS),
        )

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    def consume(self) -> None:
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])
