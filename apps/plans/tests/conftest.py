"""Fixtures das assinaturas.

Nenhum teste toca a rede: `fake_gateway` substitui o cliente do Asaas por um
duplo que grava as chamadas recebidas e devolve as mesmas estruturas da API
real (`pixQrCode`, `invoiceUrl`, `status`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from apps.payments import asaas
from apps.payments.asaas import CardSubscription, PixCharge
from apps.plans.models import Plan, PlanService

WEBHOOK_TOKEN = "segredo-de-teste"


class FakeAsaas:
    """Duplo do gateway, controlável pelo teste."""

    def __init__(self) -> None:
        self.is_configured = True
        self.calls: list[tuple[str, dict[str, Any]]] = []
        #: Status devolvido por `get_payment`, ajustável pelo teste.
        self.payment_status = "PENDING"
        self.next_payment_id = "pay-1"
        self.next_cycle_payment_id = "pay-2"
        self.cancelled_subscriptions: list[str] = []

    # --- PIX ---
    def create_pix_payment(self, **kwargs: Any) -> PixCharge:
        self.calls.append(("create_pix_payment", kwargs))
        return PixCharge(
            external_id=self.next_payment_id,
            status="PENDING",
            qr_code="00020126...br.gov.bcb.pix",
            qr_code_base64="aGVsbG8=",
            ticket_url="https://asaas.test/i/pay-1",
            expires_at=None,
            payload={"id": self.next_payment_id, "status": "PENDING"},
        )

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        self.calls.append(("get_payment", {"payment_id": payment_id}))
        return {"id": payment_id, "status": self.payment_status}

    def refund_payment(self, payment_id: str, amount: Decimal | None = None) -> dict[str, Any]:
        self.calls.append(("refund_payment", {"payment_id": payment_id}))
        return {"id": payment_id, "status": "REFUNDED"}

    # --- Cartão recorrente ---
    def create_card_subscription(self, **kwargs: Any) -> CardSubscription:
        self.calls.append(("create_card_subscription", kwargs))
        return CardSubscription(
            external_id="sub-1",
            status="ACTIVE",
            checkout_url="https://asaas.test/i/pay-1",
            first_payment_id="pay-1",
            payload={"id": "sub-1", "status": "ACTIVE"},
        )

    def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        self.calls.append(("get_subscription", {"subscription_id": subscription_id}))
        return {"id": subscription_id, "status": "ACTIVE"}

    def cancel_subscription(self, subscription_id: str) -> dict[str, Any]:
        self.cancelled_subscriptions.append(subscription_id)
        self.calls.append(("cancel_subscription", {"subscription_id": subscription_id}))
        return {"id": subscription_id, "deleted": True}

    def called(self, name: str) -> list[dict[str, Any]]:
        return [payload for call, payload in self.calls if call == name]


@pytest.fixture
def fake_gateway(monkeypatch, settings) -> FakeAsaas:
    """Injeta o duplo no único ponto de construção do cliente."""
    settings.ASAAS_API_KEY = "TEST-key"
    settings.ASAAS_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    gateway = FakeAsaas()
    monkeypatch.setattr(asaas, "get_client", lambda: gateway)
    return gateway


@pytest.fixture
def plan(db, branch, service) -> Plan:
    """Plano com 2 cortes por ciclo e 50% de desconto acima da cota."""
    plan = Plan.objects.create(
        name="Barba & Cabelo",
        description="Dois cortes por mês",
        price=Decimal("99.90"),
        overage_discount_percentage=Decimal("50.00"),
    )
    plan.branches.add(branch)
    PlanService.objects.create(plan=plan, service=service, monthly_quota=2)
    return plan


@pytest.fixture
def unlimited_plan(db, service) -> Plan:
    plan = Plan.objects.create(name="Ilimitado", price=Decimal("199.90"))
    PlanService.objects.create(plan=plan, service=service, monthly_quota=0)
    return plan
