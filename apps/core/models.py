"""Modelos base compartilhados por todas as apps da Sua Barbearia."""

from __future__ import annotations

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """Adiciona created_at / updated_at."""

    created_at = models.DateTimeField(_("criado em"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """Identificador público em UUID (o PK continua sendo BigAutoField)."""

    uuid = models.UUIDField(
        _("uuid"), default=uuid.uuid4, editable=False, unique=True, db_index=True
    )

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel):
    """Base padrão: uuid público + timestamps."""

    class Meta:
        abstract = True


class ActiveQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class ActivableModel(models.Model):
    """Entidades que podem ser desativadas em vez de excluídas."""

    is_active = models.BooleanField(_("ativo"), default=True, db_index=True)

    objects = ActiveQuerySet.as_manager()

    class Meta:
        abstract = True


class AuditAction(models.TextChoices):
    CREATE = "CREATE", _("Criação")
    UPDATE = "UPDATE", _("Atualização")
    DELETE = "DELETE", _("Exclusão")
    LOGIN = "LOGIN", _("Login")
    LOGOUT = "LOGOUT", _("Logout")
    LOGIN_FAILED = "LOGIN_FAILED", _("Falha de login")
    STATUS_CHANGE = "STATUS_CHANGE", _("Mudança de status")


class AuditLog(TimeStampedModel):
    """Trilha de auditoria das operações sensíveis do sistema."""

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name=_("usuário"),
    )
    action = models.CharField(_("ação"), max_length=20, choices=AuditAction.choices)
    entity = models.CharField(_("entidade"), max_length=100, db_index=True)
    entity_id = models.CharField(_("id da entidade"), max_length=64, blank=True, db_index=True)
    old_data = models.JSONField(_("dados anteriores"), null=True, blank=True)
    new_data = models.JSONField(_("dados novos"), null=True, blank=True)
    ip_address = models.GenericIPAddressField(_("endereço IP"), null=True, blank=True)
    user_agent = models.CharField(_("user agent"), max_length=400, blank=True)
    request_id = models.CharField(_("request id"), max_length=64, blank=True, db_index=True)

    class Meta:
        verbose_name = _("log de auditoria")
        verbose_name_plural = _("logs de auditoria")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["entity", "entity_id"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.entity}#{self.entity_id}"


class Branding(BaseModel):
    """Identidade visual do sistema: a logo exibida em todo o aplicativo.

    É um **singleton** — existe no máximo um registro, sempre com `pk=1`. A
    barbearia é uma empresa só (com várias filiais), então a logo é global; ter
    uma tabela com uma linha é mais simples de ler e de evoluir do que espalhar
    a configuração em um arquivo ou em variáveis de ambiente, e mantém o
    histórico de quem trocou e quando.

    A logo é servida também para quem **não está autenticado**: a tela de login
    precisa dela antes de existir sessão.
    """

    #: Chave fixa do único registro.
    SINGLETON_PK = 1

    company_name = models.CharField(
        _("nome da barbearia"),
        max_length=60,
        default="Sua Barbearia",
        help_text=_("Exibido na marca do aplicativo e no título da página."),
    )
    logo = models.ImageField(
        _("logo"),
        upload_to="branding/",
        null=True,
        blank=True,
        help_text=_("PNG com fundo transparente, idealmente 600x160 px."),
    )
    updated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="branding_updates",
        verbose_name=_("atualizada por"),
    )

    class Meta:
        verbose_name = _("identidade visual")
        verbose_name_plural = _("identidade visual")

    def __str__(self) -> str:
        return self.company_name

    def save(self, *args, **kwargs):
        # Força o singleton mesmo se alguém criar pelo admin ou pelo shell.
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Não some com o registro: apenas volta para a logo padrão."""
        self.clear_logo()

    def clear_logo(self) -> None:
        if self.logo:
            self.logo.delete(save=False)
        self.logo = None
        self.save(update_fields=["logo", "updated_at"])

    @classmethod
    def load(cls) -> Branding:
        """Devolve o registro único, criando-o na primeira chamada."""
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj
