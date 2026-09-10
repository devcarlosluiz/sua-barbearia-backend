"""Tarefas assíncronas de notificação (Celery)."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("suabarbearia.celery")


@shared_task(bind=True, max_retries=3, default_retry_delay=30, ignore_result=True)
def notify_appointment_event(self, appointment_id: int, notification_type: str) -> str:
    """Notifica cliente (e barbeiro, quando aplicável) sobre um evento."""
    from apps.appointments.models import Appointment
    from apps.notifications.services import notify_appointment

    appointment = (
        Appointment.objects.select_related("client__user", "barber__user", "branch", "service")
        .filter(pk=appointment_id)
        .first()
    )
    if appointment is None:
        logger.warning("Agendamento %s não encontrado para notificação.", appointment_id)
        return "appointment-not-found"

    try:
        created = notify_appointment(appointment, notification_type)
    except Exception as exc:  # pragma: no cover - falha transitória de provider
        logger.exception("Falha ao notificar o agendamento %s", appointment_id)
        raise self.retry(exc=exc) from exc

    return f"sent:{len(created)}"


@shared_task(ignore_result=True)
def send_appointment_reminders() -> str:
    """Envia lembretes nas antecedências configuradas (padrão: 24h e 2h).

    Executada periodicamente pelo Celery Beat. Cada agendamento registra em
    `reminders_sent` quais lembretes já saíram, garantindo idempotência.
    """
    from apps.appointments.models import BLOCKING_STATUSES, Appointment
    from apps.notifications.models import NotificationType
    from apps.notifications.services import notify_appointment

    now = timezone.now()
    window_minutes = 15
    total = 0

    for hours in settings.SUA_BARBEARIA["REMINDER_HOURS_BEFORE"]:
        target = now + timedelta(hours=hours)
        window_start = target - timedelta(minutes=window_minutes)
        window_end = target + timedelta(minutes=window_minutes)

        candidates = Appointment.objects.select_related(
            "client__user", "barber__user", "branch", "service"
        ).filter(
            status__in=BLOCKING_STATUSES,
            date__gte=window_start.date(),
            date__lte=window_end.date(),
        )

        for appointment in candidates:
            key = f"{hours}h"
            if key in (appointment.reminders_sent or []):
                continue
            if not (window_start <= appointment.start_datetime <= window_end):
                continue

            notify_appointment(appointment, NotificationType.APPOINTMENT_REMINDER)
            appointment.reminders_sent = [*(appointment.reminders_sent or []), key]
            appointment.save(update_fields=["reminders_sent", "updated_at"])
            total += 1

    logger.info("Lembretes enviados: %d", total)
    return f"reminders:{total}"


@shared_task(ignore_result=True)
def mark_no_show_appointments() -> str:
    """Marca como NO_SHOW agendamentos não iniciados após 30min do horário."""
    from apps.appointments.models import Appointment, AppointmentStatus
    from apps.appointments.services.booking import transition_status

    cutoff = timezone.now() - timedelta(minutes=30)
    candidates = Appointment.objects.select_related("branch").filter(
        status__in=[AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED],
        date__lte=cutoff.date(),
    )

    total = 0
    for appointment in candidates:
        if appointment.start_datetime > cutoff:
            continue
        try:
            transition_status(
                appointment=appointment,
                to_status=AppointmentStatus.NO_SHOW,
                user=None,
                reason="Marcado automaticamente pelo sistema.",
            )
            total += 1
        except Exception:  # pragma: no cover
            logger.exception("Falha ao marcar no-show do agendamento %s", appointment.pk)

    logger.info("Agendamentos marcados como no-show: %d", total)
    return f"no_show:{total}"


@shared_task(ignore_result=True)
def cleanup_old_notifications(days: int = 90) -> str:
    """Remove notificações lidas com mais de N dias."""
    from apps.notifications.models import Notification

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = Notification.objects.filter(read_at__isnull=False, created_at__lt=cutoff).delete()
    logger.info("Notificações antigas removidas: %d", deleted)
    return f"deleted:{deleted}"
