"""Clientes da Sua Barbearia."""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class Client(BaseModel):
    """Perfil do cliente, vinculado a um usuário com role CLIENT.

    Os campos agregados (`total_visits`, `total_spent`, `last_visit_at`,
    `favorite_service`) são desnormalizações mantidas pelo serviço de
    atendimento para evitar agregações caras em listagens e dashboards.
    """

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="client_profile",
        verbose_name=_("usuário"),
    )
    birth_date = models.DateField(_("data de nascimento"), null=True, blank=True)
    cpf = models.CharField(_("CPF"), max_length=11, blank=True, db_index=True)
    preferred_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preferred_by_clients",
        verbose_name=_("filial preferida"),
    )
    preferred_barber = models.ForeignKey(
        "barbers.Barber",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preferred_by_clients",
        verbose_name=_("barbeiro preferido"),
    )
    notes = models.TextField(_("observações internas"), blank=True)
    accepts_marketing = models.BooleanField(_("aceita comunicações"), default=True)

    # --- Agregados ---
    loyalty_points = models.IntegerField(_("pontos de fidelidade"), default=0)
    total_visits = models.PositiveIntegerField(_("total de visitas"), default=0)
    total_spent = models.DecimalField(
        _("total gasto"), max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    last_visit_at = models.DateTimeField(_("último atendimento"), null=True, blank=True)
    favorite_service = models.ForeignKey(
        "services.Service",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="favorite_of_clients",
        verbose_name=_("serviço favorito"),
    )

    class Meta:
        verbose_name = _("cliente")
        verbose_name_plural = _("clientes")
        ordering = ("user__first_name",)
        indexes = [
            models.Index(fields=["preferred_branch", "-last_visit_at"]),
        ]

    def __str__(self) -> str:
        return self.user.full_name

    @property
    def full_name(self) -> str:
        return self.user.full_name

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def phone(self) -> str:
        return self.user.phone

    @property
    def average_ticket(self) -> Decimal:
        if not self.total_visits:
            return Decimal("0.00")
        return (self.total_spent / self.total_visits).quantize(Decimal("0.01"))
