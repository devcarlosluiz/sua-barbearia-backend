"""Mixins reutilizáveis para ViewSets."""

from __future__ import annotations

from typing import Any

from django.db import models
from rest_framework import serializers

from apps.core.services import audit


class AuditableViewSetMixin:
    """Registra CREATE/UPDATE/DELETE no AuditLog automaticamente."""

    audit_enabled = True

    def perform_create(self, serializer: serializers.BaseSerializer) -> None:
        instance = serializer.save()
        if self.audit_enabled and isinstance(instance, models.Model):
            audit.log_create(instance, user=self.request.user)

    def perform_update(self, serializer: serializers.BaseSerializer) -> None:
        old_data = audit.serialize_instance(serializer.instance)
        instance = serializer.save()
        if self.audit_enabled and isinstance(instance, models.Model):
            audit.log_update(instance, old_data, user=self.request.user)

    def perform_destroy(self, instance: models.Model) -> None:
        if self.audit_enabled:
            audit.log_delete(instance, user=self.request.user)
        instance.delete()


class MultiSerializerMixin:
    """Permite mapear serializers por action.

    Exemplo::

        serializer_classes = {
            "list": BranchListSerializer,
            "retrieve": BranchDetailSerializer,
        }
    """

    serializer_classes: dict[str, Any] = {}

    def get_serializer_class(self):  # type: ignore[override]
        return self.serializer_classes.get(self.action, super().get_serializer_class())
