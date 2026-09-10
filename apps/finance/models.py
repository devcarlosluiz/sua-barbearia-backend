"""Financeiro: lançamentos de caixa e comissões."""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class TransactionType(models.TextChoices):
    INCOME = "INCOME", _("Receita")
    EXPENSE = "EXPENSE", _("Despesa")


class TransactionCategory(models.TextChoices):
    SERVICES = "SERVICES", _("Serviços")
    PRODUCTS = "PRODUCTS", _("Produtos")
    SUBSCRIPTION = "SUBSCRIPTION", _("Planos mensais")
    SALARY = "SALARY", _("Salários")
    COMMISSION = "COMMISSION", _("Comissões")
    RENT = "RENT", _("Aluguel")
    ELECTRICITY = "ELECTRICITY", _("Energia elétrica")
    WATER = "WATER", _("Água")
    INTERNET = "INTERNET", _("Internet")
    SUPPLIES = "SUPPLIES", _("Insumos")
    MARKETING = "MARKETING", _("Marketing")
    TAXES = "TAXES", _("Impostos")
    OTHER = "OTHER", _("Outros")


class TransactionQuerySet(models.QuerySet):
    def income(self):
        return self.filter(type=TransactionType.INCOME)

    def expense(self):
        return self.filter(type=TransactionType.EXPENSE)

    def in_period(self, start, end):
        return self.filter(date__gte=start, date__lte=end)


class Transaction(BaseModel):
    """Lançamento financeiro (receita ou despesa) de uma filial."""

    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="transactions",
        verbose_name=_("filial"),
    )
    type = models.CharField(_("tipo"), max_length=8, choices=TransactionType.choices, db_index=True)
    category = models.CharField(
        _("categoria"),
        max_length=15,
        choices=TransactionCategory.choices,
        default=TransactionCategory.OTHER,
        db_index=True,
    )
    description = models.CharField(_("descrição"), max_length=255)
    amount = models.DecimalField(
        _("valor"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    date = models.DateField(_("data"), db_index=True)
    notes = models.TextField(_("observações"), blank=True)

    # --- Rastreabilidade da origem ---
    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name=_("pagamento"),
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name=_("agendamento"),
    )
    sale = models.ForeignKey(
        "inventory.Sale",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name=_("venda"),
    )
    barber = models.ForeignKey(
        "barbers.Barber",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name=_("barbeiro"),
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_transactions",
        verbose_name=_("criado por"),
    )
    is_automatic = models.BooleanField(
        _("gerado automaticamente"),
        default=False,
        help_text=_("Lançamentos criados pelo sistema a partir de atendimentos/vendas."),
    )

    objects = TransactionQuerySet.as_manager()

    class Meta:
        verbose_name = _("lançamento financeiro")
        verbose_name_plural = _("lançamentos financeiros")
        ordering = ("-date", "-created_at")
        indexes = [
            models.Index(fields=["branch", "date", "type"]),
            models.Index(fields=["type", "category", "date"]),
        ]

    def __str__(self) -> str:
        signal = "+" if self.type == TransactionType.INCOME else "-"
        return f"{signal} R$ {self.amount} - {self.description}"

    @property
    def signed_amount(self) -> Decimal:
        return self.amount if self.type == TransactionType.INCOME else -self.amount


class CommissionStatus(models.TextChoices):
    PENDING = "PENDING", _("Pendente")
    PAID = "PAID", _("Paga")
    CANCELLED = "CANCELLED", _("Cancelada")


class Commission(BaseModel):
    """Comissão do barbeiro gerada ao concluir um atendimento."""

    barber = models.ForeignKey(
        "barbers.Barber",
        on_delete=models.PROTECT,
        related_name="commissions",
        verbose_name=_("barbeiro"),
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="commissions",
        verbose_name=_("filial"),
    )
    appointment = models.OneToOneField(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="commission",
        null=True,
        blank=True,
        verbose_name=_("agendamento"),
    )
    sale_item = models.OneToOneField(
        "inventory.SaleItem",
        on_delete=models.CASCADE,
        related_name="commission",
        null=True,
        blank=True,
        verbose_name=_("item de venda"),
    )
    base_amount = models.DecimalField(_("valor base"), max_digits=10, decimal_places=2)
    percentage = models.DecimalField(_("percentual"), max_digits=5, decimal_places=2)
    amount = models.DecimalField(_("valor da comissão"), max_digits=10, decimal_places=2)
    reference_date = models.DateField(_("data de referência"), db_index=True)
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=CommissionStatus.choices,
        default=CommissionStatus.PENDING,
        db_index=True,
    )
    paid_at = models.DateTimeField(_("paga em"), null=True, blank=True)
    payout_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commission_payouts",
        verbose_name=_("lançamento de pagamento"),
    )

    class Meta:
        verbose_name = _("comissão")
        verbose_name_plural = _("comissões")
        ordering = ("-reference_date", "-created_at")
        indexes = [
            models.Index(fields=["barber", "reference_date", "status"]),
            models.Index(fields=["branch", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.barber} - R$ {self.amount} ({self.get_status_display()})"
