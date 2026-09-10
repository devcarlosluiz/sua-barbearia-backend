"""Criação e despacho de notificações.

O despacho é feito por canal. Hoje o canal `IN_APP` é persistido no banco e o
canal `PUSH` é entregue a um provider plugável (`push.py`). E-mail e WhatsApp
são pontos de extensão já previstos pela modelagem.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from apps.notifications.models import Notification, NotificationChannel, NotificationType

logger = logging.getLogger("suabarbearia.application")


def build_appointment_message(appointment, notification_type: str) -> tuple[str, str]:
    """Título e corpo da notificação de um evento de agendamento."""
    when = f"{appointment.date:%d/%m/%Y} às {appointment.start_time:%H:%M}"
    service = appointment.service.name
    barber = appointment.barber.display_name
    branch = appointment.branch.name

    messages = {
        NotificationType.APPOINTMENT_CREATED: (
            "Agendamento realizado",
            f"Seu {service} com {barber} está marcado para {when} na filial {branch}.",
        ),
        NotificationType.APPOINTMENT_CONFIRMED: (
            "Agendamento confirmado",
            f"Confirmamos seu {service} com {barber} em {when}.",
        ),
        NotificationType.APPOINTMENT_REMINDER: (
            "Lembrete do seu horário",
            f"Seu {service} com {barber} é {when}, na filial {branch}. Te esperamos!",
        ),
        NotificationType.APPOINTMENT_CANCELLED: (
            "Agendamento cancelado",
            f"Seu {service} de {when} foi cancelado.",
        ),
        NotificationType.APPOINTMENT_RESCHEDULED: (
            "Agendamento remarcado",
            f"Seu {service} foi remarcado para {when} com {barber}.",
        ),
        NotificationType.APPOINTMENT_COMPLETED: (
            "Atendimento concluído",
            f"Obrigado pela visita! Que tal avaliar seu atendimento com {barber}?",
        ),
    }
    return messages.get(
        notification_type,
        ("Sua Barbearia", f"Atualização do seu agendamento de {when}."),
    )


def create_notification(
    *,
    user,
    notification_type: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    channels: list[str] | None = None,
    appointment=None,
) -> Notification:
    """Persiste a notificação e dispara os canais externos configurados."""
    channels = channels or [NotificationChannel.IN_APP, NotificationChannel.PUSH]
    notification = Notification.objects.create(
        user=user,
        type=notification_type,
        title=title,
        body=body,
        data=data or {},
        channels=channels,
        appointment=appointment,
        sent_at=timezone.now(),
    )

    if NotificationChannel.PUSH in channels:
        from apps.notifications.push import send_push

        send_push(user=user, title=title, body=body, data=data or {})

    return notification


def notify_appointment(appointment, notification_type: str) -> list[Notification]:
    """Notifica cliente e barbeiro sobre um evento do agendamento."""
    title, body = build_appointment_message(appointment, notification_type)
    payload = {
        "appointment_id": appointment.pk,
        "appointment_uuid": str(appointment.uuid),
        "type": notification_type,
    }

    created = [
        create_notification(
            user=appointment.client.user,
            notification_type=notification_type,
            title=title,
            body=body,
            data=payload,
            appointment=appointment,
        )
    ]

    barber_events = {
        NotificationType.APPOINTMENT_CREATED,
        NotificationType.APPOINTMENT_CANCELLED,
        NotificationType.APPOINTMENT_RESCHEDULED,
    }
    if notification_type in barber_events:
        created.append(
            create_notification(
                user=appointment.barber.user,
                notification_type=notification_type,
                title=title,
                body=(
                    f"{appointment.client.full_name} - {appointment.service.name} em "
                    f"{appointment.date:%d/%m} às {appointment.start_time:%H:%M}."
                ),
                data=payload,
                appointment=appointment,
            )
        )
    return created
