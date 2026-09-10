"""Produtos revendidos pela Sua Barbearia."""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, BaseModel


def product_image_path(instance: Product, filename: str) -> str:
    return f"products/{instance.uuid}/{filename}"


class ProductCategory(ActivableModel, BaseModel):
    name = models.CharField(_("nome"), max_length=80, unique=True)
    slug = models.SlugField(_("slug"), max_length=100, unique=True)

    class Meta:
        verbose_name = _("categoria de produto")
        verbose_name_plural = _("categorias de produto")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Product(ActivableModel, BaseModel):
    """Produto com controle de estoque por filial (ver `inventory.StockItem`)."""

    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name=_("categoria"),
    )
    name = models.CharField(_("nome"), max_length=150, db_index=True)
    description = models.TextField(_("descrição"), blank=True)
    sku = models.CharField(_("SKU"), max_length=50, unique=True, db_index=True)
    barcode = models.CharField(_("código de barras"), max_length=50, blank=True, db_index=True)
    cost_price = models.DecimalField(
        _("preço de custo"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    sale_price = models.DecimalField(
        _("preço de venda"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    commission_percentage = models.DecimalField(
        _("comissão do barbeiro (%)"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Percentual pago ao barbeiro na venda deste produto."),
    )
    image = models.ImageField(_("imagem"), upload_to=product_image_path, blank=True, null=True)
    unit = models.CharField(_("unidade"), max_length=10, default="UN")

    class Meta:
        verbose_name = _("produto")
        verbose_name_plural = _("produtos")
        ordering = ("name",)
        indexes = [
            models.Index(fields=["is_active", "name"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.sku})"

    @property
    def margin(self) -> Decimal:
        return self.sale_price - self.cost_price

    @property
    def total_stock(self) -> int:
        return sum(item.quantity for item in self.stock_items.all())
