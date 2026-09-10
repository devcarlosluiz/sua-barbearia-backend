"""Agendamentos e histórico de status."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.core.utils import combine_local


class AppointmentStatus(models.TextChoices):
    PENDING = "PENDING", _("Pendente")
    CONFIRMED = "CONFIRMED", _("Confirmado")
    ARRIVED = "ARRIVED", _("Cliente chegou")
    IN_PROGRESS = "IN_PROGRESS", _("Em atendimento")
    COMPLETED = "COMPLETED", _("Concluído")
    CANCELLED = "CANCELLED", _("Cancelado")
    NO_SHOW = "NO_SHOW", _("Não compareceu")


#: Status que ainda ocupam a agenda do barbeiro.
BLOCKING_STATUSES = (
    AppointmentStatus.PENDING,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.ARRIVED,
    AppointmentStatus.IN_PROGRESS,
)

#: Status finais — não permitem mais transição.
FINAL_STATUSES = (
    AppointmentStatus.COMPLETED,
    AppointmentStatus.CANCELLED,
    AppointmentStatus.NO_SHOW,
)

#: Máquina de estados do atendimento.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    AppointmentStatus.PENDING: (
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.ARRIVED,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.NO_SHOW,
    ),
    AppointmentStatus.CONFIRMED: (
        AppointmentStatus.ARRIVED,
        AppointmentStatus.IN_PROGRESS,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.NO_SHOW,
    ),
    AppointmentStatus.ARRIVED: (
        AppointmentStatus.IN_PROGRESS,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.NO_SHOW,
    ),
    AppointmentStatus.IN_PROGRESS: (AppointmentStatus.COMPLETED,),
    AppointmentStatus.COMPLETED: (),
    AppointmentStatus.CANCELLED: (),
    AppointmentStatus.NO_SHOW: (),
}


class CancelledBy(models.TextChoices):
    CLIENT = "CLIENT", _("Cliente")
    BARBER = "BARBER", _("Barbeiro")
    OWNER = "OWNER", _("Proprietário")
    SYSTEM = "SYSTEM", _("Sistema")


class AppointmentQuerySet(models.QuerySet):
    def blocking(self):
        """Agendamentos que ocupam a agenda."""
        return self.filter(status__in=BLOCKING_STATUSES)

    def for_day(self, day):
        return self.filter(date=day)

    def with_relations(self):
        return self.select_related("client__user", "barber__user", "branch", "service")


class Appointment(BaseModel):
    """Agendamento de um serviço com um barbeiro em uma filial."""

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name=_("cliente"),
    )
    barber = models.ForeignKey(
        "barbers.Barber",
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name=_("barbeiro"),
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name=_("filial"),
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name=_("serviço"),
    )

    date = models.DateField(_("data"), db_index=True)
    start_time = models.TimeField(_("início"))
    end_time = models.TimeField(_("fim"))
    price = models.DecimalField(_("valor"), max_digits=10, decimal_places=2)

    status = models.CharField(
        _("status"),
        max_length=12,
        choices=AppointmentStatus.choices,
        default=AppointmentStatus.PENDING,
        db_index=True,
    )
    notes = models.TextField(_("observações"), blank=True)
    internal_notes = models.TextField(_("observações internas"), blank=True)

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_appointments",
        verbose_name=_("criado por"),
    )
    rescheduled_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reschedules",
        verbose_name=_("reagendado a partir de"),
    )

    # --- Ciclo de atendimento ---
    confirmed_at = models.DateTimeField(_("confirmado em"), null=True, blank=True)
    arrived_at = models.DateTimeField(_("chegada do cliente"), null=True, blank=True)
    started_at = models.DateTimeField(_("início do atendimento"), null=True, blank=True)
    completed_at = models.DateTimeField(_("conclusão"), null=True, blank=True)

    # --- Cancelamento ---
    cancelled_at = models.DateTimeField(_("cancelado em"), null=True, blank=True)
    cancelled_by_role = models.CharField(
        _("cancelado por"), max_length=10, choices=CancelledBy.choices, blank=True
    )
    cancelled_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_appointments",
        verbose_name=_("usuário que cancelou"),
    )
    cancellation_reason = models.CharField(_("motivo do cancelamento"), max_length=300, blank=True)

    # --- Lembretes já enviados (evita duplicidade no Celery) ---
    reminders_sent = models.JSONField(_("lembretes enviados"), default=list, blank=True)

    objects = AppointmentQuerySet.as_manager()

    class Meta:
        verbose_name = _("agendamento")
        verbose_name_plural = _("agendamentos")
        ordering = ("-date", "-start_time")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="appointment_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["barber", "date", "start_time"],
                condition=models.Q(status__in=list(BLOCKING_STATUSES)),
                name="unique_active_appointment_per_barber_slot",
            ),
        ]
        indexes = [
            models.Index(fields=["branch", "date", "status"]),
            models.Index(fields=["barber", "date", "start_time"]),
            models.Index(fields=["client", "-date"]),
            models.Index(fields=["status", "date"]),
        ]

    def __str__(self) -> str:
        return f"{self.date:%d/%m/%Y} {self.start_time:%H:%M} - {self.client} com {self.barber}"

    # ------------------------------------------------------------------
    # Propriedades derivadas
    # ------------------------------------------------------------------
    @property
    def start_datetime(self) -> datetime:
        return combine_local(self.date, self.start_time)

    @property
    def end_datetime(self) -> datetime:
        return combine_local(self.date, self.end_time)

    @property
    def duration_minutes(self) -> int:
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        return end - start

    @property
    def is_blocking(self) -> bool:
        return self.status in BLOCKING_STATUSES

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES

    @property
    def commission_amount(self) -> Decimal:
        percentage = self.barber.commission_percentage
        return (self.price * percentage / Decimal("100")).quantize(Decimal("0.01"))

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in ALLOWED_TRANSITIONS.get(self.status, ())


class AppointmentStatusHistory(models.Model):
    """Histórico de transições de status e reagendamentos."""

    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name="status_history",
        verbose_name=_("agendamento"),
    )
    from_status = models.CharField(
        _("de"), max_length=12, choices=AppointmentStatus.choices, blank=True
    )
    to_status = models.CharField(_("para"), max_length=12, choices=AppointmentStatus.choices)
    changed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointment_status_changes",
        verbose_name=_("alterado por"),
    )
    reason = models.CharField(_("motivo"), max_length=300, blank=True)
    metadata = models.JSONField(_("metadados"), default=dict, blank=True)
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("histórico do agendamento")
        verbose_name_plural = _("históricos do agendamento")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.appointment_id}: {self.from_status} -> {self.to_status}"
