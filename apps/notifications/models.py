"""Notificações internas e tokens de push."""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class NotificationType(models.TextChoices):
    APPOINTMENT_CREATED = "APPOINTMENT_CREATED", _("Agendamento criado")
    APPOINTMENT_CONFIRMED = "APPOINTMENT_CONFIRMED", _("Agendamento confirmado")
    APPOINTMENT_REMINDER = "APPOINTMENT_REMINDER", _("Lembrete de agendamento")
    APPOINTMENT_CANCELLED = "APPOINTMENT_CANCELLED", _("Agendamento cancelado")
    APPOINTMENT_RESCHEDULED = "APPOINTMENT_RESCHEDULED", _("Agendamento remarcado")
    APPOINTMENT_COMPLETED = "APPOINTMENT_COMPLETED", _("Atendimento concluído")
    LOYALTY = "LOYALTY", _("Fidelidade")
    PROMOTION = "PROMOTION", _("Promoção")
    SYSTEM = "SYSTEM", _("Sistema")


class NotificationChannel(models.TextChoices):
    IN_APP = "IN_APP", _("No aplicativo")
    PUSH = "PUSH", _("Push")
    EMAIL = "EMAIL", _("E-mail")
    WHATSAPP = "WHATSAPP", _("WhatsApp")


class Notification(BaseModel):
    """Notificação destinada a um usuário."""

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("usuário"),
    )
    type = models.CharField(_("tipo"), max_length=25, choices=NotificationType.choices)
    title = models.CharField(_("título"), max_length=150)
    body = models.TextField(_("mensagem"))
    data = models.JSONField(_("dados"), default=dict, blank=True)
    channels = models.JSONField(_("canais"), default=list, blank=True)
    read_at = models.DateTimeField(_("lida em"), null=True, blank=True)
    sent_at = models.DateTimeField(_("enviada em"), null=True, blank=True)
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name=_("agendamento"),
    )

    class Meta:
        verbose_name = _("notificação")
        verbose_name_plural = _("notificações")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "read_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_type_display()} -> {self.user_id}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def mark_as_read(self) -> None:
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at", "updated_at"])


class DevicePlatform(models.TextChoices):
    ANDROID = "ANDROID", _("Android")
    IOS = "IOS", _("iOS")
    WEB = "WEB", _("Web")


class DeviceToken(BaseModel):
    """Token de push (FCM/APNs) registrado por um dispositivo do usuário."""

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="device_tokens",
        verbose_name=_("usuário"),
    )
    token = models.CharField(_("token"), max_length=255, unique=True)
    platform = models.CharField(_("plataforma"), max_length=10, choices=DevicePlatform.choices)
    device_name = models.CharField(_("dispositivo"), max_length=120, blank=True)
    is_active = models.BooleanField(_("ativo"), default=True)
    last_used_at = models.DateTimeField(_("último uso"), null=True, blank=True)

    class Meta:
        verbose_name = _("token de dispositivo")
        verbose_name_plural = _("tokens de dispositivo")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.get_platform_display()} - {self.user_id}"
