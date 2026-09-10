"""Catálogo de serviços da Sua Barbearia."""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, BaseModel


def service_image_path(instance: Service, filename: str) -> str:
    return f"services/{instance.uuid}/{filename}"


class ServiceCategory(ActivableModel, BaseModel):
    """Agrupamento de serviços (Cabelo, Barba, Estética...)."""

    name = models.CharField(_("nome"), max_length=80, unique=True)
    slug = models.SlugField(_("slug"), max_length=100, unique=True)
    display_order = models.PositiveSmallIntegerField(_("ordem de exibição"), default=0)

    class Meta:
        verbose_name = _("categoria de serviço")
        verbose_name_plural = _("categorias de serviço")
        ordering = ("display_order", "name")

    def __str__(self) -> str:
        return self.name


class Service(ActivableModel, BaseModel):
    """Serviço oferecido pela barbearia."""

    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="services",
        verbose_name=_("categoria"),
    )
    name = models.CharField(_("nome"), max_length=120, unique=True, db_index=True)
    slug = models.SlugField(_("slug"), max_length=140, unique=True)
    description = models.TextField(_("descrição"), blank=True)
    duration_minutes = models.PositiveSmallIntegerField(
        _("duração (min)"), validators=[MinValueValidator(5)]
    )
    price = models.DecimalField(
        _("preço"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    image = models.ImageField(_("imagem"), upload_to=service_image_path, blank=True, null=True)
    display_order = models.PositiveSmallIntegerField(_("ordem de exibição"), default=0)

    class Meta:
        verbose_name = _("serviço")
        verbose_name_plural = _("serviços")
        ordering = ("display_order", "name")
        indexes = [
            models.Index(fields=["is_active", "display_order"]),
        ]

    def __str__(self) -> str:
        return self.name
