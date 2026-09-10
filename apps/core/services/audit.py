"""Serviço de auditoria: registra alterações sensíveis no AuditLog."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import models

from apps.core.context import get_client_ip, get_current_user, get_request_id, get_user_agent
from apps.core.models import AuditAction, AuditLog

logger = logging.getLogger("suabarbearia.security")

SENSITIVE_FIELDS = {
    "password",
    "senha",
    "token",
    "access",
    "refresh",
    "secret",
    "api_key",
    "authorization",
}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, models.Model):
        return value.pk
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def serialize_instance(instance: models.Model, fields: list[str] | None = None) -> dict[str, Any]:
    """Converte uma instância em dict JSON-safe, mascarando dados sensíveis."""
    data: dict[str, Any] = {}
    for field in instance._meta.fields:
        name = field.name
        if fields is not None and name not in fields:
            continue
        if any(sensitive in name.lower() for sensitive in SENSITIVE_FIELDS):
            data[name] = "***"
            continue
        data[name] = _serialize_value(getattr(instance, field.attname, None))
    return data


def log_action(
    *,
    action: str,
    entity: str,
    entity_id: Any = "",
    old_data: dict[str, Any] | None = None,
    new_data: dict[str, Any] | None = None,
    user: Any | None = None,
) -> AuditLog | None:
    """Cria um registro de auditoria. Nunca deve quebrar o fluxo principal."""
    try:
        return AuditLog.objects.create(
            user=user or get_current_user(),
            action=action,
            entity=entity,
            entity_id=str(entity_id or ""),
            old_data=old_data,
            new_data=new_data,
            ip_address=get_client_ip(),
            user_agent=(get_user_agent() or "")[:400],
            request_id=get_request_id() or "",
        )
    except Exception:  # pragma: no cover - auditoria nunca derruba a operação
        logger.exception("Falha ao registrar auditoria para %s#%s", entity, entity_id)
        return None


def log_create(instance: models.Model, user: Any | None = None) -> None:
    log_action(
        action=AuditAction.CREATE,
        entity=instance._meta.label,
        entity_id=instance.pk,
        new_data=serialize_instance(instance),
        user=user,
    )


def log_update(instance: models.Model, old_data: dict[str, Any], user: Any | None = None) -> None:
    new_data = serialize_instance(instance)
    changed = {k: v for k, v in new_data.items() if old_data.get(k) != v}
    if not changed:
        return
    log_action(
        action=AuditAction.UPDATE,
        entity=instance._meta.label,
        entity_id=instance.pk,
        old_data={k: old_data.get(k) for k in changed},
        new_data=changed,
        user=user,
    )


def log_delete(instance: models.Model, user: Any | None = None) -> None:
    log_action(
        action=AuditAction.DELETE,
        entity=instance._meta.label,
        entity_id=instance.pk,
        old_data=serialize_instance(instance),
        user=user,
    )
