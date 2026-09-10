"""Webhook do Mercado Pago.

O endpoint é público, então a segurança é toda na assinatura HMAC. Estes testes
existem sobretudo para provar que **não** se aceita confirmação de pagamento de
origem não verificada — seria dar plano de graça a quem soubesse a URL.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from rest_framework.test import APIClient

from apps.plans.models import InvoiceStatus, SubscriptionStatus
from apps.plans.services import subscribe
from apps.plans.tests.conftest import WEBHOOK_SECRET

pytestmark = pytest.mark.django_db

URL = "/api/v1/webhooks/mercado-pago/"


def sign(*, data_id: str, request_id: str = "req-1", ts: str = "1700000000") -> str:
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"ts={ts},v1={digest}"


def post(client: APIClient, *, topic: str, data_id: str, signature: str | None = None):
    return client.post(
        URL,
        {"type": topic, "data": {"id": data_id}},
        format="json",
        headers={
            "x-signature": signature if signature is not None else sign(data_id=data_id),
            "x-request-id": "req-1",
        },
    )


class TestAutenticidade:
    def test_sem_assinatura_e_recusado(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
        fake_gateway.payment_status = "approved"

        response = post(api, topic="payment", data_id="pay-1", signature="")
        assert response.status_code == 401

        # E nada foi confirmado.
        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.PENDING_PAYMENT
        assert subscription.invoices.get().status == InvoiceStatus.PENDING

    def test_assinatura_adulterada_e_recusada(self, api, client_profile, plan, fake_gateway):
        subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
        fake_gateway.payment_status = "approved"

        response = post(api, topic="payment", data_id="pay-1", signature="ts=1,v1=deadbeef")
        assert response.status_code == 401

    def test_assinatura_de_outro_id_nao_serve(self, api, client_profile, plan, fake_gateway):
        """Reaproveitar a assinatura de um id em outro id não passa."""
        subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        response = post(api, topic="payment", data_id="pay-1", signature=sign(data_id="outro-id"))
        assert response.status_code == 401

    def test_sem_segredo_configurado_recusa(
        self, api, settings, client_profile, plan, fake_gateway
    ):
        """Produção sem segredo não pode virar porta aberta."""
        settings.MERCADO_PAGO_WEBHOOK_SECRET = ""
        response = post(api, topic="payment", data_id="pay-1")
        assert response.status_code == 401

    def test_webhook_nao_exige_jwt(self, api, client_profile, plan, fake_gateway):
        """Quem chama é o provedor: não há usuário autenticado."""
        subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
        fake_gateway.payment_status = "approved"

        response = post(api, topic="payment", data_id="pay-1")
        assert response.status_code == 200, response.content


class TestPagamentoPix:
    def test_aprovado_ativa_a_assinatura(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
        fake_gateway.payment_status = "approved"

        response = post(api, topic="payment", data_id="pay-1")
        assert response.status_code == 200, response.content

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.invoices.get().status == InvoiceStatus.PAID

    def test_reentrega_nao_duplica_a_receita(self, api, client_profile, plan, fake_gateway):
        from apps.finance.models import Transaction, TransactionCategory

        subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
        fake_gateway.payment_status = "approved"

        post(api, topic="payment", data_id="pay-1")
        post(api, topic="payment", data_id="pay-1")

        assert Transaction.objects.filter(category=TransactionCategory.SUBSCRIPTION).count() == 1

    def test_recusado_expira_a_fatura(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
        fake_gateway.payment_status = "rejected"

        post(api, topic="payment", data_id="pay-1")

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.PENDING_PAYMENT
        assert subscription.invoices.get().status == InvoiceStatus.EXPIRED

    def test_pendente_nao_muda_nada(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
        fake_gateway.payment_status = "pending"

        post(api, topic="payment", data_id="pay-1")

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.PENDING_PAYMENT

    def test_o_corpo_recebido_nao_decide_nada(self, api, client_profile, plan, fake_gateway):
        """Mesmo assinado, o status vem da consulta ao provedor.

        Se confiássemos no corpo, quem tivesse o segredo poderia declarar
        qualquer pagamento como aprovado.
        """
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
        fake_gateway.payment_status = "rejected"

        response = api.post(
            URL,
            {"type": "payment", "data": {"id": "pay-1"}, "status": "approved"},
            format="json",
            headers={"x-signature": sign(data_id="pay-1"), "x-request-id": "req-1"},
        )
        assert response.status_code == 200

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.PENDING_PAYMENT

    def test_pagamento_desconhecido_nao_quebra(self, api, fake_gateway):
        fake_gateway.payment_status = "approved"
        response = post(api, topic="payment", data_id="pay-999")
        assert response.status_code == 200
        assert response.json()["data"]["handled"] is False


class TestAssinaturaRecorrente:
    def test_preapproval_autorizado_ativa(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="CARD_RECURRING")
        fake_gateway.preapproval_status = "authorized"

        response = post(api, topic="preapproval", data_id="preapp-1")
        assert response.status_code == 200, response.content

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.invoices.get().status == InvoiceStatus.PAID

    def test_preapproval_cancelado_encerra(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="CARD_RECURRING")
        fake_gateway.preapproval_status = "cancelled"

        post(api, topic="preapproval", data_id="preapp-1")

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.CANCELLED

    def test_cobranca_mensal_renova_o_ciclo(self, api, client_profile, plan, fake_gateway):
        """A cobrança automática do mês seguinte estende a validade."""
        from dateutil.relativedelta import relativedelta

        subscription = subscribe(client=client_profile, plan=plan, billing_type="CARD_RECURRING")
        fake_gateway.preapproval_status = "authorized"
        post(api, topic="preapproval", data_id="preapp-1")

        subscription.refresh_from_db()
        primeiro_fim = subscription.current_period_end

        response = post(api, topic="subscription_authorized_payment", data_id="authp-1")
        assert response.status_code == 200, response.content

        subscription.refresh_from_db()
        assert subscription.current_period_start == primeiro_fim + relativedelta(days=1)
        assert subscription.current_period_end > primeiro_fim
        assert subscription.invoices.filter(status=InvoiceStatus.PAID).count() == 2

    def test_cobranca_nao_processada_e_ignorada(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="CARD_RECURRING")
        fake_gateway.preapproval_status = "authorized"
        post(api, topic="preapproval", data_id="preapp-1")
        fake_gateway.authorized_payment_status = "rejected"

        post(api, topic="subscription_authorized_payment", data_id="authp-1")

        subscription.refresh_from_db()
        assert subscription.invoices.filter(status=InvoiceStatus.PAID).count() == 1


class TestTopicoDesconhecido:
    def test_topico_nao_suportado_responde_200(self, api, fake_gateway):
        """Responder erro faria o provedor reentregar para sempre."""
        response = post(api, topic="merchant_order", data_id="mo-1")
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "unsupported_topic"

    def test_sem_id_nao_quebra(self, api, fake_gateway):
        response = post(api, topic="payment", data_id="")
        # Sem id não há manifesto válido para assinar.
        assert response.status_code in (200, 401)
