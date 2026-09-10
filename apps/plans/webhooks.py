"""Recepção de webhooks do Mercado Pago.

O endpoint é público por natureza — quem chama é o provedor, não um usuário
autenticado. Por isso a autenticidade vem da assinatura HMAC que o Mercado
Pago envia no header `x-signature`, no formato:

    x-signature: ts=1704908010,v1=<hmac_sha256>
    x-request-id: <uuid>

O manifesto assinado é `id:<data.id>;request-id:<x-request-id>;ts:<ts>;`.

Regras que valem aqui:

* **Sem segredo configurado a requisição é recusada** (503). Aceitar webhook
  não assinado deixaria qualquer um marcar fatura como paga.
* A confirmação **nunca** confia no corpo recebido: usa o `id` para consultar
  o provedor e decidir pelo status que ele responde.
* O processamento é idempotente — o Mercado Pago reentrega.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from django.conf import settings

from apps.plans.models import InvoiceStatus, SubscriptionInvoice, SubscriptionStatus

logger = logging.getLogger("suabarbearia.application")


def signature_is_valid(*, signature_header: str, request_id: str, data_id: str) -> bool:
    """Confere o HMAC do header `x-signature`."""
    secret = settings.MERCADO_PAGO_WEBHOOK_SECRET
    if not secret:
        return False

    parts = dict(
        piece.strip().split("=", 1) for piece in signature_header.split(",") if "=" in piece
    )
    timestamp = parts.get("ts", "")
    received = parts.get("v1", "")
    if not timestamp or not received:
        return False

    manifest = f"id:{data_id};request-id:{request_id};ts:{timestamp};"
    expected = hmac.new(
        secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    # `compare_digest` evita vazar informação por tempo de resposta.
    return hmac.compare_digest(expected, received)


def handle_notification(*, topic: str, data_id: str) -> dict[str, Any]:
    """Roteia a notificação para o tratador do seu tópico."""
    if not data_id:
        return {"handled": False, "reason": "missing_id"}

    if topic in ("payment", "payment.updated", "payment.created"):
        return _handle_payment(data_id)
    if topic in ("preapproval", "subscription_preapproval"):
        return _handle_preapproval(data_id)
    if topic in ("subscription_authorized_payment", "authorized_payment"):
        return _handle_authorized_payment(data_id)

    logger.info("Webhook do Mercado Pago ignorado: topico=%s", topic)
    return {"handled": False, "reason": "unsupported_topic"}


def _handle_payment(payment_id: str) -> dict[str, Any]:
    """PIX avulso: aprova, expira ou ignora a fatura correspondente."""
    from apps.payments.mercadopago import get_client
    from apps.plans.services import confirm_invoice

    payment = get_client().get_payment(payment_id)
    status = str(payment.get("status", ""))

    invoice = SubscriptionInvoice.objects.filter(external_id=str(payment_id)).first()
    if invoice is None:
        # Pode ser um pagamento fora do escopo de assinatura (venda avulsa) ou
        # a nossa fatura ainda não ter sido gravada. Reentrega resolve.
        logger.info("Webhook sem fatura correspondente: payment=%s", payment_id)
        return {"handled": False, "reason": "invoice_not_found"}

    if status == "approved":
        confirm_invoice(invoice, provider_payload=payment)
        return {"handled": True, "action": "invoice_paid"}

    if status in ("cancelled", "rejected", "expired"):
        SubscriptionInvoice.objects.filter(pk=invoice.pk, status=InvoiceStatus.PENDING).update(
            status=InvoiceStatus.EXPIRED, provider_payload=payment
        )
        return {"handled": True, "action": "invoice_expired"}

    return {"handled": True, "action": "ignored", "status": status}


def _handle_preapproval(preapproval_id: str) -> dict[str, Any]:
    """Assinatura recorrente: autorizada, pausada ou cancelada."""
    from apps.payments.mercadopago import get_client
    from apps.plans.models import Subscription
    from apps.plans.services import confirm_invoice

    data = get_client().get_preapproval(preapproval_id)
    status = str(data.get("status", ""))

    subscription = Subscription.objects.filter(external_id=str(preapproval_id)).first()
    if subscription is None:
        logger.info("Webhook sem assinatura correspondente: preapproval=%s", preapproval_id)
        return {"handled": False, "reason": "subscription_not_found"}

    if status == "authorized":
        # O cartão foi aceito: o primeiro ciclo está pago.
        invoice = (
            subscription.invoices.filter(status=InvoiceStatus.PENDING)
            .order_by("period_start")
            .first()
        )
        if invoice is not None:
            confirm_invoice(invoice, provider_payload=data)
        return {"handled": True, "action": "subscription_authorized"}

    if status in ("cancelled", "paused"):
        Subscription.objects.filter(pk=subscription.pk).exclude(
            status__in=(SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED)
        ).update(status=SubscriptionStatus.CANCELLED, provider_payload=data)
        return {"handled": True, "action": "subscription_cancelled"}

    return {"handled": True, "action": "ignored", "status": status}


def _handle_authorized_payment(authorized_payment_id: str) -> dict[str, Any]:
    """Cobrança mensal do cartão: cria e quita a fatura do novo ciclo."""
    from dateutil.relativedelta import relativedelta

    from apps.payments.mercadopago import get_client
    from apps.payments.models import PaymentMethod, PaymentProvider
    from apps.plans.models import Subscription
    from apps.plans.services import confirm_invoice, period_bounds

    data = get_client().get_authorized_payment(authorized_payment_id)
    status = str(data.get("status", ""))
    preapproval_id = str(data.get("preapproval_id", ""))

    subscription = Subscription.objects.filter(external_id=preapproval_id).first()
    if subscription is None:
        return {"handled": False, "reason": "subscription_not_found"}

    if status != "processed":
        return {"handled": True, "action": "ignored", "status": status}

    # O próximo ciclo começa no dia seguinte ao fim do atual; na primeira
    # cobrança o ciclo atual pode não existir ainda.
    if subscription.current_period_end is not None:
        next_start = subscription.current_period_end + relativedelta(days=1)
    else:
        from django.utils import timezone

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
            "provider": PaymentProvider.MERCADO_PAGO,
            "external_id": str(authorized_payment_id),
        },
    )
    confirm_invoice(invoice, provider_payload=data)
    return {"handled": True, "action": "cycle_renewed"}
