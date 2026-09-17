"""Abstração de gateway de pagamento.

O sistema nunca fala diretamente com um provedor: ele conversa com a interface
`PaymentGateway`. Hoje só existe o `ManualGateway` (registro no caixa); o
pagamento online de planos mensais fala com o Asaas diretamente em
`apps/plans/services.py`, via `apps/payments/asaas.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from django.utils import timezone

from apps.payments.models import PaymentProvider, PaymentStatus

logger = logging.getLogger("suabarbearia.application")


@dataclass
class ChargeRequest:
    amount: Decimal
    method: str
    description: str
    external_reference: str
    payer_email: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChargeResult:
    status: str
    external_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    checkout_url: str | None = None


class PaymentGateway(Protocol):
    provider: str

    def charge(self, request: ChargeRequest) -> ChargeResult: ...

    def refund(self, external_id: str, amount: Decimal) -> ChargeResult: ...


class ManualGateway:
    """Pagamento registrado manualmente no caixa da filial."""

    provider = PaymentProvider.MANUAL

    def charge(self, request: ChargeRequest) -> ChargeResult:
        return ChargeResult(
            status=PaymentStatus.PAID,
            external_id="",
            payload={
                "registered_at": timezone.now().isoformat(),
                "method": request.method,
                "amount": str(request.amount),
            },
        )

    def refund(self, external_id: str, amount: Decimal) -> ChargeResult:
        return ChargeResult(
            status=PaymentStatus.REFUNDED,
            external_id=external_id,
            payload={"refunded_at": timezone.now().isoformat(), "amount": str(amount)},
        )


GATEWAYS: dict[str, type] = {
    PaymentProvider.MANUAL: ManualGateway,
}


def get_gateway(provider: str = PaymentProvider.MANUAL) -> PaymentGateway:
    """Resolve o gateway do provider informado."""
    gateway_class = GATEWAYS.get(provider)
    if gateway_class is None:
        logger.warning("Provider '%s' não implementado; usando o gateway manual.", provider)
        gateway_class = ManualGateway
    return gateway_class()
