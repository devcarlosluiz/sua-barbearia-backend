"""Barbeiros, serviços executados, jornada de trabalho e ausências."""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.branches.models import Weekday
from apps.core.models import ActivableModel, BaseModel


class Barber(ActivableModel, BaseModel):
    """Perfil profissional vinculado a um usuário com role BARBER."""

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="barber_profile",
        verbose_name=_("usuário"),
    )
    branches = models.ManyToManyField(
        "branches.Branch",
        related_name="barbers",
        verbose_name=_("filiais"),
        help_text=_("Filiais em que o barbeiro atende."),
    )
    services = models.ManyToManyField(
        "services.Service",
        through="BarberService",
        related_name="barbers",
        verbose_name=_("serviços"),
        blank=True,
    )
    nickname = models.CharField(_("apelido"), max_length=60, blank=True)
    bio = models.TextField(_("biografia"), blank=True)
    specialties = models.JSONField(
        _("especialidades"),
        default=list,
        blank=True,
        help_text=_('Lista de textos livres, ex.: ["Degradê", "Barba terapia"]'),
    )
    commission_percentage = models.DecimalField(
        _("percentual de comissão"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("40.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    rating = models.DecimalField(
        _("avaliação média"), max_digits=3, decimal_places=2, default=Decimal("0.00")
    )
    reviews_count = models.PositiveIntegerField(_("total de avaliações"), default=0)
    hired_at = models.DateField(_("data de admissão"), null=True, blank=True)

    class Meta:
        verbose_name = _("barbeiro")
        verbose_name_plural = _("barbeiros")
        ordering = ("user__first_name",)

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.nickname or self.user.full_name

    def works_at(self, branch_id: int) -> bool:
        return self.branches.filter(pk=branch_id).exists()

    def performs(self, service_id: int) -> bool:
        return self.barber_services.filter(service_id=service_id, is_active=True).exists()


class BarberService(models.Model):
    """Associação barbeiro <-> serviço, com preço/duração opcionalmente próprios."""

    barber = models.ForeignKey(
        Barber, on_delete=models.CASCADE, related_name="barber_services", verbose_name=_("barbeiro")
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.CASCADE,
        related_name="barber_services",
        verbose_name=_("serviço"),
    )
    custom_price = models.DecimalField(
        _("preço personalizado"), max_digits=10, decimal_places=2, null=True, blank=True
    )
    custom_duration_minutes = models.PositiveSmallIntegerField(
        _("duração personalizada (min)"), null=True, blank=True
    )
    is_active = models.BooleanField(_("ativo"), default=True)

    class Meta:
        verbose_name = _("serviço do barbeiro")
        verbose_name_plural = _("serviços do barbeiro")
        ordering = ("barber", "service")
        constraints = [
            models.UniqueConstraint(
                fields=["barber", "service"], name="unique_barberservice_barber_service"
            )
        ]

    def __str__(self) -> str:
        return f"{self.barber} - {self.service}"

    @property
    def price(self) -> Decimal:
        return self.custom_price if self.custom_price is not None else self.service.price

    @property
    def duration_minutes(self) -> int:
        return self.custom_duration_minutes or self.service.duration_minutes


class WorkingHour(models.Model):
    """Jornada semanal do barbeiro em uma filial, com intervalo de almoço."""

    barber = models.ForeignKey(
        Barber, on_delete=models.CASCADE, related_name="working_hours", verbose_name=_("barbeiro")
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="working_hours",
        verbose_name=_("filial"),
    )
    weekday = models.PositiveSmallIntegerField(_("dia da semana"), choices=Weekday.choices)
    starts_at = models.TimeField(_("início"))
    ends_at = models.TimeField(_("fim"))
    break_starts_at = models.TimeField(_("início do intervalo"), null=True, blank=True)
    break_ends_at = models.TimeField(_("fim do intervalo"), null=True, blank=True)
    is_active = models.BooleanField(_("ativo"), default=True)

    class Meta:
        verbose_name = _("horário de trabalho")
        verbose_name_plural = _("horários de trabalho")
        ordering = ("barber", "weekday", "starts_at")
        constraints = [
            models.UniqueConstraint(
                fields=["barber", "branch", "weekday"],
                name="unique_workinghour_barber_branch_weekday",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="workinghour_ends_after_starts",
            ),
        ]
        indexes = [
            models.Index(fields=["barber", "weekday", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.barber} - {self.get_weekday_display()} {self.starts_at:%H:%M}-{self.ends_at:%H:%M}"

    @property
    def has_break(self) -> bool:
        return self.break_starts_at is not None and self.break_ends_at is not None


class TimeOffType(models.TextChoices):
    VACATION = "VACATION", _("Férias")
    DAY_OFF = "DAY_OFF", _("Folga")
    HOLIDAY = "HOLIDAY", _("Feriado")
    BLOCK = "BLOCK", _("Bloqueio de agenda")
    SICK_LEAVE = "SICK_LEAVE", _("Atestado")
    OTHER = "OTHER", _("Outro")


class TimeOff(BaseModel):
    """Ausência/bloqueio do barbeiro em um intervalo de tempo."""

    barber = models.ForeignKey(
        Barber, on_delete=models.CASCADE, related_name="time_offs", verbose_name=_("barbeiro")
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="time_offs",
        null=True,
        blank=True,
        verbose_name=_("filial"),
        help_text=_("Em branco = ausência em todas as filiais."),
    )
    type = models.CharField(
        _("tipo"), max_length=12, choices=TimeOffType.choices, default=TimeOffType.BLOCK
    )
    starts_at = models.DateTimeField(_("início"), db_index=True)
    ends_at = models.DateTimeField(_("fim"), db_index=True)
    reason = models.CharField(_("motivo"), max_length=200, blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_time_offs",
        verbose_name=_("criado por"),
    )

    class Meta:
        verbose_name = _("ausência")
        verbose_name_plural = _("ausências")
        ordering = ("-starts_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="timeoff_ends_after_starts",
            )
        ]
        indexes = [
            models.Index(fields=["barber", "starts_at", "ends_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.barber} - {self.get_type_display()} ({self.starts_at:%d/%m %H:%M})"


class SpecialWorkingHour(models.Model):
    """Horário especial do barbeiro em uma data específica (sobrepõe a jornada)."""

    barber = models.ForeignKey(
        Barber,
        on_delete=models.CASCADE,
        related_name="special_hours",
        verbose_name=_("barbeiro"),
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="special_hours",
        verbose_name=_("filial"),
    )
    date = models.DateField(_("data"), db_index=True)
    starts_at = models.TimeField(_("início"))
    ends_at = models.TimeField(_("fim"))
    break_starts_at = models.TimeField(_("início do intervalo"), null=True, blank=True)
    break_ends_at = models.TimeField(_("fim do intervalo"), null=True, blank=True)
    note = models.CharField(_("observação"), max_length=200, blank=True)

    class Meta:
        verbose_name = _("horário especial")
        verbose_name_plural = _("horários especiais")
        ordering = ("-date",)
        constraints = [
            models.UniqueConstraint(
                fields=["barber", "branch", "date"], name="unique_specialhour_barber_branch_date"
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="specialhour_ends_after_starts",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.barber} - {self.date:%d/%m/%Y}"

    @property
    def has_break(self) -> bool:
        return self.break_starts_at is not None and self.break_ends_at is not None
