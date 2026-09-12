"""Tarefas das assinaturas.

Quatro rotinas, todas idempotentes:

* `renew_pix_invoices` — emite o PIX do próximo ciclo antes de vencer.
* `sync_subscription_payments` — rede de segurança para webhook perdido.
* `suspend_overdue` — suspende quem passou da tolerância sem pagar.
* `poll_pix_invoice` — acompanha de perto uma cobrança recém-emitida. Esta é a
  única que não roda no beat: quem a dispara é a emissão do QR.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

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


@shared_task(name="plans.poll_pix_invoice")
def poll_pix_invoice(invoice_id: int, attempt: int = 1) -> dict[str, object]:
    """Consulta uma cobrança PIX recém-emitida até ela sair de `PENDING`.

    Reagenda a si mesma em vez de dormir em laço: um `sleep` seguraria um
    processo do worker por dez minutos a cada assinatura em curso, e bastariam
    algumas simultâneas para a fila inteira parar.

    Para sozinha assim que a fatura resolve — inclusive quando quem resolveu
    foi o webhook, porque aí ela não está mais `PENDING`.
    """
    from apps.plans.models import SubscriptionInvoice
    from apps.plans.services import sync_invoice

    invoice = SubscriptionInvoice.objects.filter(pk=invoice_id).first()
    if invoice is None:
        return {"invoice": invoice_id, "outcome": "not_found"}

    outcome = sync_invoice(invoice)
    if outcome != "pending":
        logger.info(
            "Consulta da fatura %s encerrada na tentativa %s: %s", invoice_id, attempt, outcome
        )
        return {"invoice": invoice_id, "attempt": attempt, "outcome": outcome}

    config = settings.SUBSCRIPTION_SETTINGS
    if attempt >= int(config["PIX_POLL_ATTEMPTS"]):
        # Daqui para frente quem cuida é a varredura periódica.
        logger.info("Fatura %s ainda em aberto após %s consultas", invoice_id, attempt)
        return {"invoice": invoice_id, "attempt": attempt, "outcome": "gave_up"}

    poll_pix_invoice.apply_async(
        (invoice_id, attempt + 1), countdown=int(config["PIX_POLL_INTERVAL_SECONDS"])
    )
    return {"invoice": invoice_id, "attempt": attempt, "outcome": "pending"}


@shared_task(name="plans.suspend_overdue")
def suspend_overdue() -> dict[str, int]:
    from apps.plans.services import suspend_overdue_subscriptions

    result = suspend_overdue_subscriptions()
    logger.info("Assinaturas em atraso: %s", result)
    return result
