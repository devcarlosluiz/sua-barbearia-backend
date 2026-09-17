"""Pagamentos.

A modelagem é agnóstica de gateway: `provider` + `external_id` + `payload`
permitem plugar outros provedores no futuro sem migração destrutiva. Hoje o
provider padrão é MANUAL (registro no caixa); o pagamento online (planos
mensais) é feito exclusivamente pelo Asaas.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class PaymentMethod(models.TextChoices):
    PIX = "PIX", _("PIX")
    CASH = "CASH", _("Dinheiro")
    CREDIT_CARD = "CREDIT_CARD", _("Cartão de crédito")
    DEBIT_CARD = "DEBIT_CARD", _("Cartão de débito")
    LOYALTY = "LOYALTY", _("Pontos de fidelidade")
    # Atendimento coberto pela cota de um plano mensal: o cliente não paga nada
    # no balcão porque a mensalidade já foi cobrada na assinatura.
    SUBSCRIPTION = "SUBSCRIPTION", _("Plano mensal")
    OTHER = "OTHER", _("Outro")


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", _("Pendente")
    PAID = "PAID", _("Pago")
    CANCELLED = "CANCELLED", _("Cancelado")
    REFUNDED = "REFUNDED", _("Estornado")


class PaymentProvider(models.TextChoices):
    MANUAL = "MANUAL", _("Manual / caixa")
    STRIPE = "STRIPE", _("Stripe")
    ASAAS = "ASAAS", _("Asaas")


class Payment(BaseModel):
    """Registro de pagamento de um atendimento e/ou de uma venda de produtos."""

    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name=_("filial"),
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments",
        verbose_name=_("agendamento"),
    )
    sale = models.ForeignKey(
        "inventory.Sale",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments",
        verbose_name=_("venda"),
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
        verbose_name=_("cliente"),
    )

    amount = models.DecimalField(
        _("valor"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    discount_amount = models.DecimalField(
        _("desconto"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    method = models.CharField(_("forma de pagamento"), max_length=15, choices=PaymentMethod.choices)
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )

    # --- Integração com gateways (futuro) ---
    provider = models.CharField(
        _("provedor"),
        max_length=20,
        choices=PaymentProvider.choices,
        default=PaymentProvider.MANUAL,
    )
    external_id = models.CharField(_("id externo"), max_length=120, blank=True, db_index=True)
    provider_payload = models.JSONField(_("retorno do provedor"), default=dict, blank=True)

    paid_at = models.DateTimeField(_("pago em"), null=True, blank=True, db_index=True)
    refunded_at = models.DateTimeField(_("estornado em"), null=True, blank=True)
    notes = models.CharField(_("observação"), max_length=300, blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registered_payments",
        verbose_name=_("registrado por"),
    )

    class Meta:
        verbose_name = _("pagamento")
        verbose_name_plural = _("pagamentos")
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(appointment__isnull=False) | models.Q(sale__isnull=False),
                name="payment_requires_appointment_or_sale",
            )
        ]
        indexes = [
            models.Index(fields=["branch", "status", "-paid_at"]),
            models.Index(fields=["method", "-paid_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_method_display()} R$ {self.amount} ({self.get_status_display()})"

    @property
    def net_amount(self) -> Decimal:
        return self.amount - self.discount_amount
