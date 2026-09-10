"""Cálculo de disponibilidade de horários.

Um horário só é ofertado quando TODAS as condições abaixo são verdadeiras:

1. A filial está aberta no dia (`OpeningHour`, com `BranchHoliday` sobrepondo).
2. O barbeiro tem jornada naquele dia/filial (`WorkingHour`), possivelmente
   sobreposta por um `SpecialWorkingHour` da data.
3. O intervalo do serviço não colide com o horário de almoço.
4. Não há `TimeOff` cobrindo o intervalo.
5. Não há agendamento com status bloqueante sobrepondo o intervalo.
6. O serviço cabe inteiro antes do fim do expediente e do fechamento da filial.
7. O horário é futuro e está dentro da antecedência máxima da filial.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.barbers.models import Barber, SpecialWorkingHour, TimeOff, WorkingHour
from apps.branches.models import Branch, BranchHoliday, OpeningHour
from apps.core.exceptions import BusinessError
from apps.core.utils import combine_local, minutes_to_time, time_to_minutes
from apps.services.models import Service

#: Antecedência mínima para agendar no mesmo dia.
MIN_LEAD_MINUTES = 15


def time_off_branch_filter(branch_id: int) -> Q:
    """Ausência vale para a filial informada ou para todas (branch nulo)."""
    return Q(branch_id=branch_id) | Q(branch__isnull=True)


@dataclass(frozen=True)
class Window:
    """Janela de trabalho em minutos a partir da meia-noite."""

    start: int
    end: int
    break_start: int | None = None
    break_end: int | None = None


@dataclass(frozen=True)
class Interval:
    start: int
    end: int

    def overlaps(self, other: Interval) -> bool:
        return self.start < other.end and other.start < self.end


class AvailabilityService:
    """Serviço de consulta de horários livres de um barbeiro."""

    def __init__(self, branch: Branch, barber: Barber, service: Service, day: date_cls) -> None:
        self.branch = branch
        self.barber = barber
        self.service = service
        self.day = day

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def validate_context(self) -> None:
        """Valida a combinação filial/barbeiro/serviço antes de calcular slots."""
        if not self.branch.is_active:
            raise BusinessError("Esta filial não está disponível.", code="BRANCH_INACTIVE")
        if not self.barber.is_active or not self.barber.user.is_active:
            raise BusinessError("Este barbeiro não está disponível.", code="BARBER_INACTIVE")
        if not self.service.is_active:
            raise BusinessError("Este serviço não está disponível.", code="SERVICE_INACTIVE")
        if not self.barber.branches.filter(pk=self.branch.pk).exists():
            raise BusinessError(
                "Este barbeiro não atende na filial selecionada.",
                code="BARBER_NOT_IN_BRANCH",
            )
        if not self.barber.barber_services.filter(
            service_id=self.service.pk, is_active=True
        ).exists():
            raise BusinessError(
                "Este barbeiro não realiza o serviço selecionado.",
                code="BARBER_DOES_NOT_PERFORM_SERVICE",
            )

        today = timezone.localdate()
        if self.day < today:
            raise BusinessError("Não é possível agendar em datas passadas.", code="DATE_IN_PAST")
        limit = today + timedelta(days=self.branch.max_advance_booking_days)
        if self.day > limit:
            raise BusinessError(
                f"Agendamentos são liberados com até {self.branch.max_advance_booking_days} "
                "dias de antecedência.",
                code="DATE_TOO_FAR",
            )

    def duration_minutes(self) -> int:
        link = self.barber.barber_services.filter(service_id=self.service.pk).first()
        if link is not None:
            return link.duration_minutes
        return self.service.duration_minutes

    def price(self):
        link = self.barber.barber_services.filter(service_id=self.service.pk).first()
        if link is not None:
            return link.price
        return self.service.price

    def available_slots(self) -> list[str]:
        """Retorna os horários livres no formato `HH:MM`."""
        self.validate_context()

        window = self.work_window()
        if window is None:
            return []

        duration = self.duration_minutes()
        step = max(self.branch.slot_interval_minutes, 5)
        busy = self.busy_intervals()
        earliest = self._earliest_start_minute()

        slots: list[str] = []
        current = self._align(window.start, step)
        while current + duration <= window.end:
            candidate = Interval(current, current + duration)
            if current >= earliest and not self._is_blocked(candidate, window, busy):
                slots.append(f"{minutes_to_time(current):%H:%M}")
            current += step
        return slots

    def is_slot_available(self, start: time, exclude_appointment_id: int | None = None) -> bool:
        """Verifica um horário específico (usado na criação/reagendamento)."""
        window = self.work_window()
        if window is None:
            return False

        duration = self.duration_minutes()
        start_minute = time_to_minutes(start)
        candidate = Interval(start_minute, start_minute + duration)

        if candidate.start < window.start or candidate.end > window.end:
            return False
        if start_minute < self._earliest_start_minute():
            return False

        busy = self.busy_intervals(exclude_appointment_id=exclude_appointment_id)
        return not self._is_blocked(candidate, window, busy)

    # ------------------------------------------------------------------
    # Janelas de trabalho
    # ------------------------------------------------------------------
    def work_window(self) -> Window | None:
        """Interseção entre o expediente da filial e a jornada do barbeiro."""
        branch_window = self._branch_window()
        if branch_window is None:
            return None
        barber_window = self._barber_window()
        if barber_window is None:
            return None

        start = max(branch_window.start, barber_window.start)
        end = min(branch_window.end, barber_window.end)
        if end <= start:
            return None
        return Window(
            start=start,
            end=end,
            break_start=barber_window.break_start,
            break_end=barber_window.break_end,
        )

    def _branch_window(self) -> Window | None:
        holiday = BranchHoliday.objects.filter(branch=self.branch, date=self.day).first()
        if holiday is not None:
            if holiday.is_fully_closed:
                return None
            return Window(time_to_minutes(holiday.opens_at), time_to_minutes(holiday.closes_at))

        opening = OpeningHour.objects.filter(branch=self.branch, weekday=self.day.weekday()).first()
        if opening is None or opening.is_closed:
            return None
        return Window(time_to_minutes(opening.opens_at), time_to_minutes(opening.closes_at))

    def _barber_window(self) -> Window | None:
        special = SpecialWorkingHour.objects.filter(
            barber=self.barber, branch=self.branch, date=self.day
        ).first()
        if special is not None:
            return Window(
                time_to_minutes(special.starts_at),
                time_to_minutes(special.ends_at),
                time_to_minutes(special.break_starts_at) if special.has_break else None,
                time_to_minutes(special.break_ends_at) if special.has_break else None,
            )

        working = WorkingHour.objects.filter(
            barber=self.barber,
            branch=self.branch,
            weekday=self.day.weekday(),
            is_active=True,
        ).first()
        if working is None:
            return None
        return Window(
            time_to_minutes(working.starts_at),
            time_to_minutes(working.ends_at),
            time_to_minutes(working.break_starts_at) if working.has_break else None,
            time_to_minutes(working.break_ends_at) if working.has_break else None,
        )

    # ------------------------------------------------------------------
    # Ocupação
    # ------------------------------------------------------------------
    def busy_intervals(self, exclude_appointment_id: int | None = None) -> list[Interval]:
        """Agendamentos bloqueantes + ausências convertidos em minutos do dia."""
        appointments = Appointment.objects.blocking().filter(barber=self.barber, date=self.day)
        if exclude_appointment_id is not None:
            appointments = appointments.exclude(pk=exclude_appointment_id)

        intervals = [
            Interval(time_to_minutes(item.start_time), time_to_minutes(item.end_time))
            for item in appointments.only("start_time", "end_time")
        ]
        intervals.extend(self._time_off_intervals())
        return intervals

    def _time_off_intervals(self) -> list[Interval]:
        day_start = combine_local(self.day, time.min)
        day_end = day_start + timedelta(days=1)

        time_offs = TimeOff.objects.filter(
            barber=self.barber, starts_at__lt=day_end, ends_at__gt=day_start
        ).filter(time_off_branch_filter(self.branch.pk))

        intervals: list[Interval] = []
        for off in time_offs:
            start_local = timezone.localtime(off.starts_at)
            end_local = timezone.localtime(off.ends_at)
            start_minute = (
                0 if start_local.date() < self.day else time_to_minutes(start_local.time())
            )
            end_minute = (
                24 * 60 if end_local.date() > self.day else time_to_minutes(end_local.time())
            )
            if end_minute > start_minute:
                intervals.append(Interval(start_minute, end_minute))
        return intervals

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------
    def _earliest_start_minute(self) -> int:
        """No dia corrente, exige uma antecedência mínima."""
        now = timezone.localtime()
        if self.day != now.date():
            return 0
        return time_to_minutes((now + timedelta(minutes=MIN_LEAD_MINUTES)).time())

    @staticmethod
    def _align(minute: int, step: int) -> int:
        remainder = minute % step
        return minute if remainder == 0 else minute + (step - remainder)

    @staticmethod
    def _is_blocked(candidate: Interval, window: Window, busy: list[Interval]) -> bool:
        has_break = window.break_start is not None and window.break_end is not None
        if has_break and candidate.overlaps(Interval(window.break_start, window.break_end)):
            return True
        return any(candidate.overlaps(interval) for interval in busy)


def build_availability(
    branch_id: int, barber_id: int, service_id: int, day: date_cls
) -> AvailabilityService:
    """Carrega as entidades e devolve o serviço de disponibilidade pronto."""
    branch = Branch.objects.filter(pk=branch_id).first()
    if branch is None:
        raise BusinessError("Filial não encontrada.", code="BRANCH_NOT_FOUND", status_code=404)

    barber = (
        Barber.objects.select_related("user")
        .prefetch_related("barber_services")
        .filter(pk=barber_id)
        .first()
    )
    if barber is None:
        raise BusinessError("Barbeiro não encontrado.", code="BARBER_NOT_FOUND", status_code=404)

    service = Service.objects.filter(pk=service_id).first()
    if service is None:
        raise BusinessError("Serviço não encontrado.", code="SERVICE_NOT_FOUND", status_code=404)

    return AvailabilityService(branch=branch, barber=barber, service=service, day=day)


def slot_end_time(start: time, duration_minutes: int) -> time:
    """Calcula o fim do atendimento a partir do início e da duração."""
    end = datetime.combine(date_cls(2000, 1, 1), start) + timedelta(minutes=duration_minutes)
    return end.time()
