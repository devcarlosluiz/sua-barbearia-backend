"""Tarefas periódicas das assinaturas.

Três rotinas, todas idempotentes:

* `renew_pix_invoices` — emite o PIX do próximo ciclo antes de vencer.
* `sync_subscription_payments` — rede de segurança para webhook perdido.
* `suspend_overdue` — suspende quem passou da tolerância sem pagar.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("suabarbearia.application")


@shared_task(name="plans.renew_pix_invoices")
def renew_pix_invoices() -> dict[str, int]:
    from apps.plans.services import renew_pix_subscriptions

    result = renew_pix_subscriptions()
    logger.info("Renovação de assinaturas PIX: %s", result)
    return result


@shared_task(name="plans.sync_subscription_payments")
def sync_subscription_payments() -> dict[str, int]:
    from apps.plans.services import expire_stale_invoices, sync_pending_invoices

    expired = expire_stale_invoices()
    result = sync_pending_invoices()
    result["expired"] = expired
    logger.info("Sincronização de faturas: %s", result)
    return result


@shared_task(name="plans.suspend_overdue")
def suspend_overdue() -> dict[str, int]:
    from apps.plans.services import suspend_overdue_subscriptions

    result = suspend_overdue_subscriptions()
    logger.info("Assinaturas em atraso: %s", result)
    return result
