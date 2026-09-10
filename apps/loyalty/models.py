"""Programa de fidelidade da Sua Barbearia."""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, BaseModel


class LoyaltyAccount(BaseModel):
    """Carteira de pontos do cliente (saldo é sempre derivado das transações)."""

    client = models.OneToOneField(
        "clients.Client",
        on_delete=models.CASCADE,
        related_name="loyalty_account",
        verbose_name=_("cliente"),
    )
    balance = models.IntegerField(_("saldo de pontos"), default=0)
    lifetime_earned = models.IntegerField(_("pontos acumulados (total)"), default=0)
    lifetime_redeemed = models.IntegerField(_("pontos resgatados (total)"), default=0)

    class Meta:
        verbose_name = _("conta de fidelidade")
        verbose_name_plural = _("contas de fidelidade")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(balance__gte=0), name="loyaltyaccount_balance_not_negative"
            )
        ]

    def __str__(self) -> str:
        return f"{self.client} - {self.balance} pts"


class LoyaltyTransactionType(models.TextChoices):
    EARN = "EARN", _("Acúmulo")
    REDEEM = "REDEEM", _("Resgate")
    ADJUSTMENT = "ADJUSTMENT", _("Ajuste")
    EXPIRED = "EXPIRED", _("Expiração")


class LoyaltyTransaction(BaseModel):
    """Movimento de pontos. `points` é positivo em EARN e negativo em REDEEM."""

    account = models.ForeignKey(
        LoyaltyAccount,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name=_("conta"),
    )
    type = models.CharField(_("tipo"), max_length=12, choices=LoyaltyTransactionType.choices)
    points = models.IntegerField(_("pontos"))
    balance_after = models.IntegerField(_("saldo após"), default=0)
    description = models.CharField(_("descrição"), max_length=255, blank=True)
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_transactions",
        verbose_name=_("agendamento"),
    )
    sale = models.ForeignKey(
        "inventory.Sale",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_transactions",
        verbose_name=_("venda"),
    )
    reward = models.ForeignKey(
        "loyalty.LoyaltyReward",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name=_("recompensa"),
    )
    expires_at = models.DateTimeField(_("expira em"), null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_transactions",
        verbose_name=_("registrado por"),
    )

    class Meta:
        verbose_name = _("movimento de fidelidade")
        verbose_name_plural = _("movimentos de fidelidade")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["account", "-created_at"]),
            models.Index(fields=["type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_type_display()} {self.points:+d} pts"


class LoyaltyRewardType(models.TextChoices):
    DISCOUNT_FIXED = "DISCOUNT_FIXED", _("Desconto em reais")
    DISCOUNT_PERCENT = "DISCOUNT_PERCENT", _("Desconto percentual")
    FREE_SERVICE = "FREE_SERVICE", _("Serviço gratuito")


class LoyaltyReward(ActivableModel, BaseModel):
    """Recompensa resgatável com pontos (ex.: 500 pts = R$ 20 de desconto)."""

    name = models.CharField(_("nome"), max_length=120)
    description = models.TextField(_("descrição"), blank=True)
    type = models.CharField(
        _("tipo"),
        max_length=20,
        choices=LoyaltyRewardType.choices,
        default=LoyaltyRewardType.DISCOUNT_FIXED,
    )
    points_cost = models.PositiveIntegerField(
        _("custo em pontos"), validators=[MinValueValidator(1)]
    )
    discount_value = models.DecimalField(
        _("valor do desconto"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_rewards",
        verbose_name=_("serviço"),
        help_text=_("Obrigatório quando o tipo é serviço gratuito."),
    )
    valid_until = models.DateField(_("válida até"), null=True, blank=True)

    class Meta:
        verbose_name = _("recompensa")
        verbose_name_plural = _("recompensas")
        ordering = ("points_cost",)

    def __str__(self) -> str:
        return f"{self.name} ({self.points_cost} pts)"
