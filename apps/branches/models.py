"""Filiais da Sua Barbearia.

A Sua Barbearia é uma única empresa com N filiais. Toda operação (agendamento,
venda, caixa, estoque) é sempre vinculada a uma filial.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, BaseModel


class Weekday(models.IntegerChoices):
    """Segue a convenção do Python: `date.weekday()` (segunda = 0)."""

    MONDAY = 0, _("Segunda-feira")
    TUESDAY = 1, _("Terça-feira")
    WEDNESDAY = 2, _("Quarta-feira")
    THURSDAY = 3, _("Quinta-feira")
    FRIDAY = 4, _("Sexta-feira")
    SATURDAY = 5, _("Sábado")
    SUNDAY = 6, _("Domingo")


class BrazilianState(models.TextChoices):
    AC = "AC", "Acre"
    AL = "AL", "Alagoas"
    AP = "AP", "Amapá"
    AM = "AM", "Amazonas"
    BA = "BA", "Bahia"
    CE = "CE", "Ceará"
    DF = "DF", "Distrito Federal"
    ES = "ES", "Espírito Santo"
    GO = "GO", "Goiás"
    MA = "MA", "Maranhão"
    MT = "MT", "Mato Grosso"
    MS = "MS", "Mato Grosso do Sul"
    MG = "MG", "Minas Gerais"
    PA = "PA", "Pará"
    PB = "PB", "Paraíba"
    PR = "PR", "Paraná"
    PE = "PE", "Pernambuco"
    PI = "PI", "Piauí"
    RJ = "RJ", "Rio de Janeiro"
    RN = "RN", "Rio Grande do Norte"
    RS = "RS", "Rio Grande do Sul"
    RO = "RO", "Rondônia"
    RR = "RR", "Roraima"
    SC = "SC", "Santa Catarina"
    SP = "SP", "São Paulo"
    SE = "SE", "Sergipe"
    TO = "TO", "Tocantins"


def branch_cover_path(instance: Branch, filename: str) -> str:
    return f"branches/{instance.uuid}/{filename}"


class Branch(ActivableModel, BaseModel):
    """Unidade física da Sua Barbearia."""

    name = models.CharField(_("nome"), max_length=120, unique=True)
    slug = models.SlugField(_("slug"), max_length=140, unique=True)
    cnpj = models.CharField(_("CNPJ"), max_length=14, blank=True, db_index=True)

    # --- Endereço ---
    address = models.CharField(_("endereço"), max_length=200)
    number = models.CharField(_("número"), max_length=20)
    complement = models.CharField(_("complemento"), max_length=100, blank=True)
    district = models.CharField(_("bairro"), max_length=100)
    city = models.CharField(_("cidade"), max_length=100, db_index=True)
    state = models.CharField(_("estado"), max_length=2, choices=BrazilianState.choices)
    zip_code = models.CharField(_("CEP"), max_length=8)
    latitude = models.DecimalField(
        _("latitude"), max_digits=10, decimal_places=7, null=True, blank=True
    )
    longitude = models.DecimalField(
        _("longitude"), max_digits=10, decimal_places=7, null=True, blank=True
    )

    # --- Contato ---
    phone = models.CharField(_("telefone"), max_length=20, blank=True)
    whatsapp = models.CharField(_("WhatsApp"), max_length=20, blank=True)
    email = models.EmailField(_("e-mail"), blank=True)
    cover_image = models.ImageField(
        _("imagem de capa"), upload_to=branch_cover_path, blank=True, null=True
    )

    # --- Configurações operacionais ---
    slot_interval_minutes = models.PositiveSmallIntegerField(
        _("intervalo entre horários (min)"),
        default=30,
        validators=[MinValueValidator(5)],
        help_text=_("Granularidade da grade de horários exibida ao cliente."),
    )
    cancellation_limit_hours = models.PositiveSmallIntegerField(
        _("limite para cancelamento (horas)"),
        default=2,
        help_text=_("Antecedência mínima para o cliente cancelar sem penalidade."),
    )
    max_advance_booking_days = models.PositiveSmallIntegerField(
        _("antecedência máxima de agendamento (dias)"), default=90
    )
    loyalty_points_per_currency_unit = models.DecimalField(
        _("pontos por R$ 1,00"), max_digits=6, decimal_places=2, default=Decimal("1.00")
    )
    loyalty_point_value = models.DecimalField(
        _("valor de 1 ponto (R$)"), max_digits=6, decimal_places=4, default=Decimal("0.0400")
    )

    class Meta:
        verbose_name = _("filial")
        verbose_name_plural = _("filiais")
        ordering = ("name",)
        indexes = [
            models.Index(fields=["city", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def full_address(self) -> str:
        parts = [f"{self.address}, {self.number}"]
        if self.complement:
            parts.append(self.complement)
        parts.append(f"{self.district} - {self.city}/{self.state}")
        return " - ".join(parts)


class OpeningHour(models.Model):
    """Horário de funcionamento da filial por dia da semana."""

    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="opening_hours", verbose_name=_("filial")
    )
    weekday = models.PositiveSmallIntegerField(_("dia da semana"), choices=Weekday.choices)
    opens_at = models.TimeField(_("abre às"))
    closes_at = models.TimeField(_("fecha às"))
    is_closed = models.BooleanField(_("fechado"), default=False)

    class Meta:
        verbose_name = _("horário de funcionamento")
        verbose_name_plural = _("horários de funcionamento")
        ordering = ("branch", "weekday", "opens_at")
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "weekday"], name="unique_openinghour_branch_weekday"
            ),
            models.CheckConstraint(
                condition=models.Q(closes_at__gt=models.F("opens_at")),
                name="openinghour_closes_after_opens",
            ),
        ]

    def __str__(self) -> str:
        if self.is_closed:
            return f"{self.branch} - {self.get_weekday_display()}: fechado"
        return f"{self.branch} - {self.get_weekday_display()}: {self.opens_at:%H:%M}-{self.closes_at:%H:%M}"


class BranchHoliday(models.Model):
    """Feriados e fechamentos pontuais da filial."""

    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="holidays", verbose_name=_("filial")
    )
    date = models.DateField(_("data"), db_index=True)
    description = models.CharField(_("descrição"), max_length=120)
    opens_at = models.TimeField(_("abre às"), null=True, blank=True)
    closes_at = models.TimeField(_("fecha às"), null=True, blank=True)

    class Meta:
        verbose_name = _("feriado da filial")
        verbose_name_plural = _("feriados da filial")
        ordering = ("-date",)
        constraints = [
            models.UniqueConstraint(fields=["branch", "date"], name="unique_holiday_branch_date")
        ]

    def __str__(self) -> str:
        return f"{self.branch} - {self.date:%d/%m/%Y} - {self.description}"

    @property
    def is_fully_closed(self) -> bool:
        return self.opens_at is None or self.closes_at is None
