"""Sinais da app de clientes: garante a conta de fidelidade do cliente."""

from __future__ import annotations

from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.clients.models import Client


@receiver(post_save, sender=Client)
def ensure_loyalty_account(
    sender: type[Client], instance: Client, created: bool, **kwargs: Any
) -> None:
    if not created:
        return
    from apps.loyalty.models import LoyaltyAccount

    LoyaltyAccount.objects.get_or_create(client=instance)
