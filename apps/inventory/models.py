"""Estoque por filial, movimentações e vendas de produtos.

Regra invariável: o saldo de estoque (`StockItem.quantity`) nunca é alterado
sem que exista um `StockMovement` correspondente.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class StockItem(models.Model):
    """Saldo de um produto em uma filial."""

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="stock_items",
        verbose_name=_("produto"),
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="stock_items",
        verbose_name=_("filial"),
    )
    quantity = models.IntegerField(_("quantidade"), default=0)
    minimum_stock = models.PositiveIntegerField(_("estoque mínimo"), default=0)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("saldo de estoque")
        verbose_name_plural = _("saldos de estoque")
        ordering = ("branch", "product__name")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "branch"], name="unique_stockitem_product_branch"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0), name="stockitem_quantity_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} @ {self.branch}: {self.quantity}"

    @property
    def is_below_minimum(self) -> bool:
        return self.quantity <= self.minimum_stock


class StockMovementType(models.TextChoices):
    ENTRY = "ENTRY", _("Entrada")
    SALE = "SALE", _("Venda")
    ADJUSTMENT = "ADJUSTMENT", _("Ajuste")
    LOSS = "LOSS", _("Perda")
    RETURN = "RETURN", _("Devolução")


#: Sinal aplicado ao saldo por tipo de movimento.
MOVEMENT_SIGN: dict[str, int] = {
    StockMovementType.ENTRY: 1,
    StockMovementType.RETURN: 1,
    StockMovementType.SALE: -1,
    StockMovementType.LOSS: -1,
    StockMovementType.ADJUSTMENT: 0,  # ajuste define o saldo final explicitamente
}


class StockMovement(BaseModel):
    """Histórico imutável de toda alteração de estoque."""

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        verbose_name=_("produto"),
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        verbose_name=_("filial"),
    )
    type = models.CharField(_("tipo"), max_length=12, choices=StockMovementType.choices)
    quantity = models.IntegerField(_("quantidade movimentada"))
    previous_quantity = models.IntegerField(_("saldo anterior"))
    new_quantity = models.IntegerField(_("saldo posterior"))
    unit_cost = models.DecimalField(
        _("custo unitário"), max_digits=10, decimal_places=2, null=True, blank=True
    )
    reason = models.CharField(_("motivo"), max_length=255, blank=True)
    sale_item = models.ForeignKey(
        "inventory.SaleItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
        verbose_name=_("item de venda"),
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
        verbose_name=_("registrado por"),
    )

    class Meta:
        verbose_name = _("movimentação de estoque")
        verbose_name_plural = _("movimentações de estoque")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["product", "branch", "-created_at"]),
            models.Index(fields=["type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_type_display()} {self.quantity}x {self.product}"


class SaleStatus(models.TextChoices):
    OPEN = "OPEN", _("Aberta")
    COMPLETED = "COMPLETED", _("Concluída")
    CANCELLED = "CANCELLED", _("Cancelada")


class Sale(BaseModel):
    """Venda de produtos, avulsa ou vinculada a um atendimento."""

    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="sales",
        verbose_name=_("filial"),
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales",
        verbose_name=_("cliente"),
    )
    barber = models.ForeignKey(
        "barbers.Barber",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales",
        verbose_name=_("barbeiro"),
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales",
        verbose_name=_("agendamento"),
    )
    status = models.CharField(
        _("status"), max_length=10, choices=SaleStatus.choices, default=SaleStatus.OPEN
    )
    subtotal = models.DecimalField(
        _("subtotal"), max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    discount_amount = models.DecimalField(
        _("desconto"), max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    total = models.DecimalField(
        _("total"), max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    notes = models.CharField(_("observação"), max_length=300, blank=True)
    completed_at = models.DateTimeField(_("concluída em"), null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_sales",
        verbose_name=_("registrada por"),
    )

    class Meta:
        verbose_name = _("venda")
        verbose_name_plural = _("vendas")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["branch", "status", "-completed_at"]),
        ]

    def __str__(self) -> str:
        return f"Venda {self.uuid} - R$ {self.total}"

    def recalculate(self) -> None:
        """Recalcula subtotal/total a partir dos itens."""
        self.subtotal = sum((item.total for item in self.items.all()), Decimal("0.00"))
        self.total = self.subtotal - self.discount_amount


class SaleItem(models.Model):
    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="items", verbose_name=_("venda")
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="sale_items",
        verbose_name=_("produto"),
    )
    quantity = models.PositiveIntegerField(_("quantidade"), validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(_("preço unitário"), max_digits=10, decimal_places=2)
    total = models.DecimalField(_("total"), max_digits=12, decimal_places=2)
    commission_percentage = models.DecimalField(
        _("comissão (%)"), max_digits=5, decimal_places=2, default=Decimal("0.00")
    )

    class Meta:
        verbose_name = _("item da venda")
        verbose_name_plural = _("itens da venda")
        ordering = ("id",)

    def __str__(self) -> str:
        return f"{self.quantity}x {self.product}"
