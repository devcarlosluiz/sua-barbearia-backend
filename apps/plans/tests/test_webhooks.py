"""Webhook do Asaas.

O endpoint é público, então a segurança é toda no token estático. Estes testes
existem sobretudo para provar que **não** se aceita confirmação de pagamento de
origem não verificada — seria dar plano de graça a quem soubesse a URL.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.plans.models import InvoiceStatus, SubscriptionStatus
from apps.plans.services import subscribe
from apps.plans.tests.conftest import WEBHOOK_TOKEN

pytestmark = pytest.mark.django_db

URL = "/api/v1/webhooks/asaas/"


def post(
    client: APIClient, *, event: str, payment_id: str, token: str | None = None, extra: dict | None = None
):
    payment = {"id": payment_id, **(extra or {})}
    return client.post(
        URL,
        {"event": event, "payment": payment},
        format="json",
        headers={"asaas-access-token": token if token is not None else WEBHOOK_TOKEN},
    )


class TestAutenticidade:
    def test_sem_token_e_recusado(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        response = post(api, event="PAYMENT_CONFIRMED", payment_id="pay-1", token="")
        assert response.status_code == 401

        # E nada foi confirmado.
        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.PENDING_PAYMENT
        assert subscription.invoices.get().status == InvoiceStatus.PENDING

    def test_token_errado_e_recusado(self, api, client_profile, plan, fake_gateway):
        subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        response = post(api, event="PAYMENT_CONFIRMED", payment_id="pay-1", token="outro-token")
        assert response.status_code == 401

    def test_sem_segredo_configurado_recusa(
        self, api, settings, client_profile, plan, fake_gateway
    ):
        """Produção sem segredo não pode virar porta aberta."""
        settings.ASAAS_WEBHOOK_TOKEN = ""
        response = post(api, event="PAYMENT_CONFIRMED", payment_id="pay-1")
        assert response.status_code == 401

    def test_webhook_nao_exige_jwt(self, api, client_profile, plan, fake_gateway):
        """Quem chama é o provedor: não há usuário autenticado."""
        subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        response = post(api, event="PAYMENT_CONFIRMED", payment_id="pay-1")
        assert response.status_code == 200, response.content


class TestPagamentoPix:
    def test_confirmado_ativa_a_assinatura(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        response = post(api, event="PAYMENT_CONFIRMED", payment_id="pay-1")
        assert response.status_code == 200, response.content

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.invoices.get().status == InvoiceStatus.PAID

    def test_recebido_ativa_a_assinatura(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        response = post(api, event="PAYMENT_RECEIVED", payment_id="pay-1")
        assert response.status_code == 200, response.content

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.ACTIVE

    def test_reentrega_nao_duplica_a_receita(self, api, client_profile, plan, fake_gateway):
        from apps.finance.models import Transaction, TransactionCategory

        subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        post(api, event="PAYMENT_CONFIRMED", payment_id="pay-1")
        post(api, event="PAYMENT_CONFIRMED", payment_id="pay-1")

        assert Transaction.objects.filter(category=TransactionCategory.SUBSCRIPTION).count() == 1

    def test_vencido_expira_a_fatura(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        post(api, event="PAYMENT_OVERDUE", payment_id="pay-1")

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.PENDING_PAYMENT
        assert subscription.invoices.get().status == InvoiceStatus.EXPIRED

    def test_excluido_expira_a_fatura(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        post(api, event="PAYMENT_DELETED", payment_id="pay-1")

        subscription.refresh_from_db()
        assert subscription.invoices.get().status == InvoiceStatus.EXPIRED

    def test_pagamento_desconhecido_nao_quebra(self, api, fake_gateway):
        response = post(api, event="PAYMENT_CONFIRMED", payment_id="pay-999")
        assert response.status_code == 200
        assert response.json()["data"]["handled"] is False


class TestAssinaturaRecorrente:
    def test_primeiro_ciclo_confirmado_ativa(self, api, client_profile, plan, fake_gateway):
        subscription = subscribe(client=client_profile, plan=plan, billing_type="CARD_RECURRING")

        response = post(api, event="PAYMENT_CONFIRMED", payment_id="pay-1")
        assert response.status_code == 200, response.content

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.invoices.get().status == InvoiceStatus.PAID

    def test_cobranca_do_proximo_ciclo_renova(self, api, client_profile, plan, fake_gateway):
        """A cobrança automática do mês seguinte estende a validade."""
        from dateutil.relativedelta import relativedelta

        subscription = subscribe(client=client_profile, plan=plan, billing_type="CARD_RECURRING")
        post(api, event="PAYMENT_CONFIRMED", payment_id="pay-1")

        subscription.refresh_from_db()
        primeiro_fim = subscription.current_period_end

        response = post(
            api,
            event="PAYMENT_CONFIRMED",
            payment_id="pay-2",
            extra={"subscription": "sub-1"},
        )
        assert response.status_code == 200, response.content

        subscription.refresh_from_db()
        assert subscription.current_period_start == primeiro_fim + relativedelta(days=1)
        assert subscription.current_period_end > primeiro_fim
        assert subscription.invoices.filter(status=InvoiceStatus.PAID).count() == 2


class TestEventoDesconhecido:
    def test_evento_nao_suportado_responde_200(self, api, fake_gateway):
        """Responder erro faria o provedor reentregar para sempre."""
        response = post(api, event="PAYMENT_CREATED", payment_id="pay-1")
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "unsupported_event"

    def test_sem_id_nao_quebra(self, api, fake_gateway):
        response = api.post(
            URL,
            {"event": "PAYMENT_CONFIRMED", "payment": {}},
            format="json",
            headers={"asaas-access-token": WEBHOOK_TOKEN},
        )
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "missing_id"
