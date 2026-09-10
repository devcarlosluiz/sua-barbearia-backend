"""Camada de push notification (provider plugável).

Sem credenciais do Firebase configuradas, o envio é apenas registrado em log —
o restante do sistema funciona normalmente. Para habilitar o FCM, defina
`FIREBASE_PROJECT_ID`/`FIREBASE_CREDENTIALS_JSON` e implemente `FCMPushProvider`.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("suabarbearia.application")


class PushProvider(Protocol):
    def send(self, tokens: list[str], title: str, body: str, data: dict[str, Any]) -> int: ...


class LoggingPushProvider:
    """Provider padrão de desenvolvimento: apenas registra o envio."""

    def send(self, tokens: list[str], title: str, body: str, data: dict[str, Any]) -> int:
        logger.info(
            "PUSH (dry-run) para %d dispositivo(s): %s | %s | data=%s",
            len(tokens),
            title,
            body,
            data,
        )
        return len(tokens)


def get_provider() -> PushProvider:
    if getattr(settings, "FIREBASE_PROJECT_ID", ""):  # pragma: no cover
        logger.debug("Firebase configurado, mas o provider FCM ainda não foi habilitado.")
    return LoggingPushProvider()


def send_push(*, user, title: str, body: str, data: dict[str, Any]) -> int:
    """Envia push para todos os dispositivos ativos do usuário."""
    from apps.notifications.models import DeviceToken

    device_tokens = list(
        DeviceToken.objects.filter(user=user, is_active=True).values_list("token", flat=True)
    )
    if not device_tokens:
        return 0

    sent = get_provider().send(device_tokens, title, body, data)
    DeviceToken.objects.filter(user=user, is_active=True).update(last_used_at=timezone.now())
    return sent
