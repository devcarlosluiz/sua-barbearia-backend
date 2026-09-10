"""Planos mensais e assinaturas dos clientes.

Modelagem em quatro peças:

* `Plan` + `PlanService` — o que o proprietário vende. A cota é por serviço e
  por ciclo; `monthly_quota=0` significa ilimitado.
* `Subscription` — o vínculo do cliente com um plano. Guarda o preço em
  *snapshot*: reajustar o plano não muda o que quem já assinou paga.
* `SubscriptionInvoice` — uma cobrança por ciclo. É ela que carrega o QR do PIX
  ou a referência da assinatura recorrente no gateway.
* `SubscriptionUsage` — consumo da cota, um registro por atendimento coberto.

Nada aqui fala com o Mercado Pago: os campos `provider` / `external_id` /
`provider_payload` seguem o mesmo padrão agnóstico já usado em `payments`.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActiveQuerySet, BaseModel
from apps.payments.models import PaymentProvider


class Plan(BaseModel):
    """Plano mensal oferecido pela barbearia."""

    name = models.CharField(_("nome"), max_length=120)
    description = models.TextField(_("descrição"), blank=True)
    price = models.DecimalField(
        _("mensalidade"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    branches = models.ManyToManyField(
        "branches.Branch",
        related_name="plans",
        blank=True,
        verbose_name=_("filiais"),
        help_text=_("Filiais onde o plano é válido. Vazio significa todas."),
    )
    overage_discount_percentage = models.DecimalField(
        _("desconto acima da cota"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
        help_text=_("Desconto aplicado ao serviço quando a cota do ciclo já foi usada."),
    )
    pays_barber_commission = models.BooleanField(
        _("paga comissão ao barbeiro"),
        default=True,
        help_text=_(
            "Quando ligado, o atendimento coberto pelo plano gera comissão sobre "
            "o preço do serviço, mesmo o cliente não pagando nada no balcão."
        ),
    )
    is_active = models.BooleanField(_("ativo"), default=True, db_index=True)
    is_public = models.BooleanField(
        _("visível ao cliente"),
        default=True,
        help_text=_("Desligue para manter o plano apenas para assinatura manual."),
    )

    objects = ActiveQuerySet.as_manager()

    class Meta:
        verbose_name = _("plano")
        verbose_name_plural = _("planos")
        ordering = ("price", "name")
        constraints = [
            models.UniqueConstraint(fields=["name"], name="unique_plan_name"),
        ]

    def __str__(self) -> str:
        return f"{self.name} (R$ {self.price}/mês)"

    @property
    def is_available(self) -> bool:
        """Pode ser assinado pelo cliente no app."""
        return self.is_active and self.is_public

    def covers_branch(self, branch_id: int | None) -> bool:
        """Plano sem filiais explícitas vale em toda a rede."""
        if branch_id is None:
            return True
        branch_ids = [branch.id for branch in self.branches.all()]
        return not branch_ids or branch_id in branch_ids


class PlanService(models.Model):
    """Serviço incluído no plano, com a cota do ciclo."""

    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name="plan_services", verbose_name=_("plano")
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.CASCADE,
        related_name="plan_services",
        verbose_name=_("serviço"),
    )
    monthly_quota = models.PositiveSmallIntegerField(
        _("cota mensal"),
        default=1,
        help_text=_("Quantos usos por ciclo. Zero significa ilimitado."),
    )

    class Meta:
        verbose_name = _("serviço do plano")
        verbose_name_plural = _("serviços do plano")
        ordering = ("plan", "service")
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "service"], name="unique_planservice_plan_service"
            ),
        ]

    def __str__(self) -> str:
        quota = "ilimitado" if self.is_unlimited else f"{self.monthly_quota}x/mês"
        return f"{self.service} - {quota}"

    @property
    def is_unlimited(self) -> bool:
        return self.monthly_quota == 0


class SubscriptionStatus(models.TextChoices):
    PENDING_PAYMENT = "PENDING_PAYMENT", _("Aguardando pagamento")
    ACTIVE = "ACTIVE", _("Ativa")
    PAST_DUE = "PAST_DUE", _("Pagamento atrasado")
    CANCELLED = "CANCELLED", _("Cancelada")
    EXPIRED = "EXPIRED", _("Expirada")


#: Estados em que o cliente "tem" uma assinatura — usados para impedir duas ao
#: mesmo tempo e para decidir se o benefício vale.
LIVE_SUBSCRIPTION_STATUSES = (
    SubscriptionStatus.PENDING_PAYMENT,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
)


class BillingType(models.TextChoices):
    CARD_RECURRING = "CARD_RECURRING", _("Cartão de crédito (recorrente)")
    PIX_MONTHLY = "PIX_MONTHLY", _("PIX (mês a mês)")


class Subscription(BaseModel):
    """Assinatura de um plano por um cliente.

    O Mercado Pago só faz cobrança recorrente automática no cartão; por isso o
    PIX é modelado como uma cobrança nova a cada ciclo (`PIX_MONTHLY`).
    """

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="subscriptions",
        verbose_name=_("cliente"),
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, related_name="subscriptions", verbose_name=_("plano")
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
        verbose_name=_("filial de preferência"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.PENDING_PAYMENT,
        db_index=True,
    )
    billing_type = models.CharField(
        _("forma de cobrança"), max_length=20, choices=BillingType.choices
    )
    price = models.DecimalField(
        _("mensalidade contratada"),
        max_digits=10,
        decimal_places=2,
        help_text=_("Copiada do plano na assinatura: reajustes não afetam quem já assinou."),
    )

    current_period_start = models.DateField(_("início do ciclo"), null=True, blank=True)
    current_period_end = models.DateField(_("fim do ciclo"), null=True, blank=True, db_index=True)

    provider = models.CharField(
        _("provedor"),
        max_length=20,
        choices=PaymentProvider.choices,
        default=PaymentProvider.MANUAL,
    )
    external_id = models.CharField(
        _("id externo"),
        max_length=120,
        blank=True,
        db_index=True,
        help_text=_("`preapproval_id` no Mercado Pago, quando a cobrança é recorrente."),
    )
    provider_payload = models.JSONField(_("retorno do provedor"), default=dict, blank=True)

    started_at = models.DateTimeField(_("ativada em"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelada em"), null=True, blank=True)
    cancel_at_period_end = models.BooleanField(
        _("cancelar no fim do ciclo"),
        default=False,
        help_text=_("O cliente cancelou mas segue com o benefício até o ciclo pago terminar."),
    )
    notes = models.CharField(_("observação"), max_length=300, blank=True)

    class Meta:
        verbose_name = _("assinatura")
        verbose_name_plural = _("assinaturas")
        ordering = ("-created_at",)
        constraints = [
            # Uma assinatura viva por cliente. Trocar de plano exige cancelar a
            # atual, o que evita duas cotas somadas e cobrança dupla.
            models.UniqueConstraint(
                fields=["client"],
                condition=models.Q(status__in=LIVE_SUBSCRIPTION_STATUSES),
                name="unique_live_subscription_per_client",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "current_period_end"]),
            models.Index(fields=["plan", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.client.full_name} - {self.plan.name} ({self.get_status_display()})"

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_SUBSCRIPTION_STATUSES

    @property
    def grants_benefit(self) -> bool:
        """A cota só vale com ciclo pago e dentro da validade."""
        if self.status != SubscriptionStatus.ACTIVE:
            return False
        if self.current_period_end is None:
            return False
        return self.current_period_end >= timezone.localdate()

    @property
    def is_recurring(self) -> bool:
        return self.billing_type == BillingType.CARD_RECURRING


class InvoiceStatus(models.TextChoices):
    PENDING = "PENDING", _("Aguardando pagamento")
    PAID = "PAID", _("Paga")
    CANCELLED = "CANCELLED", _("Cancelada")
    EXPIRED = "EXPIRED", _("Expirada")
    REFUNDED = "REFUNDED", _("Estornada")


class SubscriptionInvoice(BaseModel):
    """Cobrança de um ciclo da assinatura."""

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="invoices",
        verbose_name=_("assinatura"),
    )
    period_start = models.DateField(_("início do ciclo"))
    period_end = models.DateField(_("fim do ciclo"))
    amount = models.DecimalField(
        _("valor"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    method = models.CharField(_("forma de pagamento"), max_length=15)
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.PENDING,
        db_index=True,
    )
    due_date = models.DateField(_("vencimento"), db_index=True)

    provider = models.CharField(
        _("provedor"),
        max_length=20,
        choices=PaymentProvider.choices,
        default=PaymentProvider.MANUAL,
    )
    external_id = models.CharField(_("id externo"), max_length=120, blank=True, db_index=True)
    provider_payload = models.JSONField(_("retorno do provedor"), default=dict, blank=True)

    # Dados de checkout devolvidos pelo gateway. Não são segredo: o QR do PIX é
    # feito para ser exibido, e o `checkout_url` é a página pública do provedor.
    pix_qr_code = models.TextField(_("PIX copia e cola"), blank=True)
    pix_qr_code_base64 = models.TextField(_("QR code (base64)"), blank=True)
    checkout_url = models.URLField(_("url de checkout"), max_length=500, blank=True)
    expires_at = models.DateTimeField(_("expira em"), null=True, blank=True)

    paid_at = models.DateTimeField(_("paga em"), null=True, blank=True, db_index=True)
    transaction = models.ForeignKey(
        "finance.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscription_invoices",
        verbose_name=_("lançamento no caixa"),
    )
    confirmed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_subscription_invoices",
        verbose_name=_("confirmada por"),
    )

    class Meta:
        verbose_name = _("fatura de assinatura")
        verbose_name_plural = _("faturas de assinatura")
        ordering = ("-period_start", "-created_at")
        constraints = [
            # Um ciclo, uma cobrança em aberto ou paga. Reemitir um PIX
            # expirado substitui a cobrança em vez de duplicar o mês.
            models.UniqueConstraint(
                fields=["subscription", "period_start"],
                condition=models.Q(status__in=("PENDING", "PAID")),
                name="unique_open_invoice_per_period",
            ),
            models.CheckConstraint(
                condition=models.Q(period_end__gt=models.F("period_start")),
                name="invoice_period_end_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "due_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.subscription.plan.name} {self.period_start:%m/%Y} - R$ {self.amount}"

    @property
    def is_open(self) -> bool:
        return self.status == InvoiceStatus.PENDING


class SubscriptionUsage(models.Model):
    """Consumo de uma cota do plano por um atendimento.

    `appointment` é único: um atendimento nunca consome duas vezes, mesmo que a
    finalização seja repetida por retentativa.
    """

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="usages",
        verbose_name=_("assinatura"),
    )
    appointment = models.OneToOneField(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="subscription_usage",
        verbose_name=_("atendimento"),
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.PROTECT,
        related_name="subscription_usages",
        verbose_name=_("serviço"),
    )
    period_start = models.DateField(_("ciclo"), db_index=True)
    covered_amount = models.DecimalField(
        _("valor coberto"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    used_at = models.DateTimeField(_("usado em"), auto_now_add=True)

    class Meta:
        verbose_name = _("uso do plano")
        verbose_name_plural = _("usos do plano")
        ordering = ("-used_at",)
        indexes = [
            models.Index(fields=["subscription", "period_start", "service"]),
        ]

    def __str__(self) -> str:
        return f"{self.subscription.client.full_name} - {self.service.name} ({self.period_start})"
