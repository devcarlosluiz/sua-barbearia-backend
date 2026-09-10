"""Avaliações de atendimento."""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class Review(BaseModel):
    """Avaliação de um atendimento concluído. Uma por agendamento."""

    appointment = models.OneToOneField(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="review",
        verbose_name=_("agendamento"),
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("cliente"),
    )
    barber = models.ForeignKey(
        "barbers.Barber",
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("barbeiro"),
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("filial"),
    )
    rating = models.PositiveSmallIntegerField(
        _("nota"), validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(_("comentário"), blank=True)
    is_published = models.BooleanField(_("publicada"), default=True)
    reply = models.TextField(_("resposta da barbearia"), blank=True)
    replied_at = models.DateTimeField(_("respondida em"), null=True, blank=True)

    class Meta:
        verbose_name = _("avaliação")
        verbose_name_plural = _("avaliações")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["barber", "-created_at"]),
            models.Index(fields=["branch", "rating"]),
        ]

    def __str__(self) -> str:
        return f"{self.barber} - {self.rating}/5"
