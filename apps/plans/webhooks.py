"""Recepção de webhooks do Asaas.

O endpoint é público por natureza — quem chama é o provedor, não um usuário
autenticado. Por isso a autenticidade vem de um token estático, o mesmo
configurado ao cadastrar o webhook no painel do Asaas, que o provedor devolve
em toda chamada no header:

    asaas-access-token: <token>

Não é uma assinatura HMAC sobre o corpo — é comparação direta do token, por
isso a checagem usa `hmac.compare_digest` só para não vazar informação por
tempo de resposta.

Regras que valem aqui:

* **Sem token configurado a requisição é recusada** (503 na prática vira 401
  aqui). Aceitar webhook sem token deixaria qualquer um marcar fatura como
  paga.
* O corpo da notificação já traz o pagamento (`payment`) com o status atual,
  sem exigir uma segunda consulta ao provedor; o `provider_payload` gravado é
  sempre o que o webhook enviou.
* O processamento é idempotente — o Asaas reentrega notificações.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from django.conf import settings

from apps.plans.models import InvoiceStatus, SubscriptionInvoice

logger = logging.getLogger("suabarbearia.application")

#: Eventos que confirmam o recebimento do valor.
_PAID_EVENTS = frozenset({"PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"})
#: Eventos que encerram a cobrança sem pagamento.
_CLOSED_EVENTS = frozenset({"PAYMENT_OVERDUE", "PAYMENT_DELETED", "PAYMENT_REFUNDED"})


def signature_is_valid(*, token: str) -> bool:
    """Confere o token estático enviado no header `asaas-access-token`."""
    secret = settings.ASAAS_WEBHOOK_TOKEN
    if not secret:
        return False
    return hmac.compare_digest(secret, token or "")


def handle_notification(*, event: str, payment: dict[str, Any]) -> dict[str, Any]:
    """Roteia a notificação de acordo com o evento."""
    payment_id = str(payment.get("id") or "")
    if not payment_id:
        return {"handled": False, "reason": "missing_id"}

    if event in _PAID_EVENTS:
        return _handle_paid(payment_id, payment)
    if event in _CLOSED_EVENTS:
        return _handle_closed(payment_id, payment)

    logger.info("Webhook do Asaas ignorado: evento=%s", event)
    return {"handled": False, "reason": "unsupported_event"}


def _handle_paid(payment_id: str, payment: dict[str, Any]) -> dict[str, Any]:
    """PIX ou ciclo do cartão: confirma a fatura correspondente.

    Quando a fatura ainda não existe aqui, é a primeira notícia de um novo
    ciclo do cartão recorrente — o Asaas gera o pagamento do próximo mês antes
    de avisarmos, então a fatura é criada na hora a partir da assinatura.
    """
    from apps.plans.services import confirm_invoice

    invoice = SubscriptionInvoice.objects.filter(external_id=payment_id).first()
    if invoice is None:
        invoice = _invoice_for_new_cycle(payment)

    if invoice is None:
        logger.info("Webhook sem fatura correspondente: payment=%s", payment_id)
        return {"handled": False, "reason": "invoice_not_found"}

    confirm_invoice(invoice, provider_payload=payment)
    return {"handled": True, "action": "invoice_paid"}


def _handle_closed(payment_id: str, payment: dict[str, Any]) -> dict[str, Any]:
    """PIX vencido, cobrança excluída ou estornada: expira a fatura em aberto."""
    updated = SubscriptionInvoice.objects.filter(
        external_id=payment_id, status=InvoiceStatus.PENDING
    ).update(status=InvoiceStatus.EXPIRED, provider_payload=payment)
    if not updated:
        return {"handled": False, "reason": "invoice_not_found"}
    return {"handled": True, "action": "invoice_expired"}


def _invoice_for_new_cycle(payment: dict[str, Any]) -> SubscriptionInvoice | None:
    """Cria a fatura do próximo ciclo do cartão a partir do pagamento recebido."""
    from dateutil.relativedelta import relativedelta
    from django.utils import timezone

    from apps.payments.models import PaymentMethod
    from apps.plans.models import Subscription
    from apps.plans.services import period_bounds

    subscription_id = str(payment.get("subscription") or "")
    if not subscription_id:
        return None

    subscription = Subscription.objects.filter(external_id=subscription_id).first()
    if subscription is None:
        return None

    if subscription.current_period_end is not None:
        next_start = subscription.current_period_end + relativedelta(days=1)
    else:
        next_start = timezone.localdate()
    start, end = period_bounds(next_start)

    invoice, _ = SubscriptionInvoice.objects.get_or_create(
        subscription=subscription,
        period_start=start,
        defaults={
            "period_end": end,
            "amount": subscription.price,
            "method": PaymentMethod.CREDIT_CARD,
            "status": InvoiceStatus.PENDING,
            "due_date": start,
            "provider": subscription.provider,
            "external_id": str(payment.get("id") or ""),
        },
    )
    return invoice
