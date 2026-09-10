"""Fixtures das assinaturas.

Nenhum teste toca a rede: `fake_gateway` substitui o cliente do Mercado Pago
por um duplo que grava as chamadas recebidas e devolve as mesmas estruturas da
API real (`point_of_interaction.transaction_data`, `init_point`, `status`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from apps.payments import mercadopago
from apps.payments.mercadopago import PixCharge, Preapproval
from apps.plans.models import Plan, PlanService

WEBHOOK_SECRET = "segredo-de-teste"


class FakeMercadoPago:
    """Duplo do gateway, controlável pelo teste."""

    def __init__(self) -> None:
        self.is_configured = True
        self.calls: list[tuple[str, dict[str, Any]]] = []
        #: Status devolvido por `get_payment`, ajustável pelo teste.
        self.payment_status = "pending"
        self.preapproval_status = "pending"
        self.authorized_payment_status = "processed"
        self.next_payment_id = "pay-1"
        self.cancelled_preapprovals: list[str] = []

    # --- PIX ---
    def create_pix_payment(self, **kwargs: Any) -> PixCharge:
        self.calls.append(("create_pix_payment", kwargs))
        return PixCharge(
            external_id=self.next_payment_id,
            status="pending",
            qr_code="00020126...br.gov.bcb.pix",
            qr_code_base64="aGVsbG8=",
            ticket_url="https://mp.test/ticket/pay-1",
            expires_at=None,
            payload={"id": self.next_payment_id, "status": "pending"},
        )

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        self.calls.append(("get_payment", {"payment_id": payment_id}))
        return {"id": payment_id, "status": self.payment_status}

    def refund_payment(self, payment_id: str, amount: Decimal | None = None) -> dict[str, Any]:
        self.calls.append(("refund_payment", {"payment_id": payment_id}))
        return {"id": payment_id, "status": "refunded"}

    # --- Cartão recorrente ---
    def create_preapproval(self, **kwargs: Any) -> Preapproval:
        self.calls.append(("create_preapproval", kwargs))
        return Preapproval(
            external_id="preapp-1",
            status="pending",
            init_point="https://mp.test/checkout/preapp-1",
            payload={"id": "preapp-1", "status": "pending"},
        )

    def get_preapproval(self, preapproval_id: str) -> dict[str, Any]:
        self.calls.append(("get_preapproval", {"preapproval_id": preapproval_id}))
        return {"id": preapproval_id, "status": self.preapproval_status}

    def cancel_preapproval(self, preapproval_id: str) -> dict[str, Any]:
        self.cancelled_preapprovals.append(preapproval_id)
        self.calls.append(("cancel_preapproval", {"preapproval_id": preapproval_id}))
        return {"id": preapproval_id, "status": "cancelled"}

    def get_authorized_payment(self, authorized_payment_id: str) -> dict[str, Any]:
        self.calls.append(
            ("get_authorized_payment", {"authorized_payment_id": authorized_payment_id})
        )
        return {
            "id": authorized_payment_id,
            "status": self.authorized_payment_status,
            "preapproval_id": "preapp-1",
        }

    def called(self, name: str) -> list[dict[str, Any]]:
        return [payload for call, payload in self.calls if call == name]


@pytest.fixture
def fake_gateway(monkeypatch, settings) -> FakeMercadoPago:
    """Injeta o duplo no único ponto de construção do cliente."""
    settings.MERCADO_PAGO_ACCESS_TOKEN = "TEST-token"
    settings.MERCADO_PAGO_WEBHOOK_SECRET = WEBHOOK_SECRET
    gateway = FakeMercadoPago()
    monkeypatch.setattr(mercadopago, "get_client", lambda: gateway)
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
