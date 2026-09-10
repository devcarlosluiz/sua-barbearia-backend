"""Fluxo de assinatura: contratar, pagar, usar a cota e cancelar."""

from __future__ import annotations

from decimal import Decimal

import pytest
from dateutil.relativedelta import relativedelta
from django.utils import timezone

from apps.core.testing import data_of
from apps.plans.models import (
    BillingType,
    InvoiceStatus,
    Subscription,
    SubscriptionInvoice,
    SubscriptionStatus,
)
from apps.plans.services import confirm_invoice, period_bounds, subscribe

pytestmark = pytest.mark.django_db


class TestAssinaturaPorPix:
    def test_cliente_assina_e_recebe_o_qr(self, auth, client_profile, plan, fake_gateway):
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/subscriptions/subscribe/",
            {"plan": plan.id, "billing_type": BillingType.PIX_MONTHLY},
            format="json",
        )
        assert response.status_code == 201, response.content
        payload = data_of(response)

        # Aguardando pagamento: o benefício ainda não vale.
        assert payload["status"] == SubscriptionStatus.PENDING_PAYMENT
        assert payload["grants_benefit"] is False
        assert payload["price"] == "99.90"

        invoice = payload["open_invoice"]
        assert invoice["method"] == "PIX"
        assert invoice["pix_qr_code"].startswith("00020126")
        assert invoice["pix_qr_code_base64"] == "aGVsbG8="

        # O valor enviado ao provedor é o do plano.
        charge = fake_gateway.called("create_pix_payment")[0]
        assert charge["amount"] == Decimal("99.90")
        assert charge["payer_email"] == client_profile.user.email

    def test_pagamento_confirmado_ativa_e_define_o_ciclo(
        self, auth, client_profile, plan, fake_gateway
    ):
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        invoice = subscription.invoices.get()

        confirm_invoice(invoice)

        subscription.refresh_from_db()
        start, end = period_bounds(timezone.localdate())
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.current_period_start == start
        assert subscription.current_period_end == end
        assert subscription.grants_benefit is True

    def test_confirmacao_lanca_receita_no_caixa(self, client_profile, plan, fake_gateway):
        from apps.finance.models import Transaction, TransactionCategory, TransactionType

        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        confirm_invoice(subscription.invoices.get())

        entry = Transaction.objects.get(category=TransactionCategory.SUBSCRIPTION)
        assert entry.type == TransactionType.INCOME
        assert entry.amount == Decimal("99.90")
        assert plan.name in entry.description

    def test_confirmar_duas_vezes_nao_duplica_a_receita(self, client_profile, plan, fake_gateway):
        """O Mercado Pago reentrega webhooks: a confirmação é idempotente."""
        from apps.finance.models import Transaction, TransactionCategory

        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        invoice = subscription.invoices.get()

        confirm_invoice(invoice)
        confirm_invoice(invoice)

        assert Transaction.objects.filter(category=TransactionCategory.SUBSCRIPTION).count() == 1

    def test_reemitir_pix_substitui_a_cobranca_do_ciclo(
        self, auth, client_profile, plan, fake_gateway
    ):
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        fake_gateway.next_payment_id = "pay-2"

        api = auth(client_profile.user)
        response = api.post(f"/api/v1/subscriptions/{subscription.id}/renew-pix/")
        assert response.status_code == 200, response.content

        # Um ciclo, uma cobrança em aberto: a antiga vira expirada.
        invoices = subscription.invoices.order_by("created_at")
        assert invoices.count() == 2
        assert [i.status for i in invoices] == [InvoiceStatus.EXPIRED, InvoiceStatus.PENDING]
        assert data_of(response)["id"] == invoices.last().id


class TestAssinaturaPorCartao:
    def test_cartao_devolve_o_checkout_do_provedor(self, auth, client_profile, plan, fake_gateway):
        """O cartão é digitado no site do Mercado Pago, não no app."""
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/subscriptions/subscribe/",
            {"plan": plan.id, "billing_type": BillingType.CARD_RECURRING},
            format="json",
        )
        assert response.status_code == 201, response.content
        payload = data_of(response)

        invoice = payload["open_invoice"]
        assert invoice["method"] == "CREDIT_CARD"
        assert invoice["checkout_url"] == "https://mp.test/checkout/preapp-1"

        # Nenhum dado de cartão trafega pela nossa API, e o retorno bruto do
        # provedor não vaza para o app.
        body = str(response.content, "utf-8").lower()
        for leak in ("card_number", "security_code", "card_token", "provider_payload"):
            assert leak not in body, f"'{leak}' não pode aparecer na resposta"

        subscription = Subscription.objects.get(pk=payload["id"])
        assert subscription.external_id == "preapp-1"
        # Também não pedimos dado de cartão ao provedor: só o e-mail do pagador.
        preapproval = fake_gateway.called("create_preapproval")[0]
        assert set(preapproval) == {
            "amount",
            "reason",
            "external_reference",
            "payer_email",
        }

    def test_cancelar_recorrente_avisa_o_provedor(self, auth, client_profile, plan, fake_gateway):
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.CARD_RECURRING
        )

        api = auth(client_profile.user)
        response = api.post(f"/api/v1/subscriptions/{subscription.id}/cancel/", {}, format="json")
        assert response.status_code == 200, response.content
        assert fake_gateway.cancelled_preapprovals == ["preapp-1"]


class TestRegrasDeAssinatura:
    def test_duas_assinaturas_vivas_sao_bloqueadas(
        self, auth, client_profile, plan, unlimited_plan, fake_gateway
    ):
        subscribe(client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY)

        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/subscriptions/subscribe/",
            {"plan": unlimited_plan.id, "billing_type": BillingType.PIX_MONTHLY},
            format="json",
        )
        assert response.status_code == 409
        assert "SUBSCRIPTION_ALREADY_EXISTS" in str(response.content, "utf-8")

    def test_plano_oculto_nao_pode_ser_assinado_por_link_direto(
        self, auth, client_profile, plan, fake_gateway
    ):
        plan.is_public = False
        plan.save(update_fields=["is_public"])

        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/subscriptions/subscribe/",
            {"plan": plan.id, "billing_type": BillingType.PIX_MONTHLY},
            format="json",
        )
        assert response.status_code == 400

    def test_plano_fora_da_filial_e_recusado(self, auth, client_profile, plan, fake_gateway):
        from apps.branches.models import Branch

        outra = Branch.objects.create(name="Unidade 2", city="Curitiba", state="PR")

        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/subscriptions/subscribe/",
            {
                "plan": plan.id,
                "billing_type": BillingType.PIX_MONTHLY,
                "branch": outra.id,
            },
            format="json",
        )
        assert response.status_code == 400
        assert "PLAN_BRANCH_NOT_ALLOWED" in str(response.content, "utf-8")

    def test_barbeiro_nao_assina(self, auth, barber, plan, fake_gateway):
        api = auth(barber.user)
        response = api.post(
            "/api/v1/subscriptions/subscribe/",
            {"plan": plan.id, "billing_type": BillingType.PIX_MONTHLY},
            format="json",
        )
        assert response.status_code == 403


class TestEscopoPorPapel:
    def test_cliente_ve_so_a_propria_assinatura(
        self, auth, client_profile, other_client, plan, unlimited_plan, fake_gateway
    ):
        minha = subscribe(client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY)
        subscribe(client=other_client, plan=unlimited_plan, billing_type=BillingType.PIX_MONTHLY)

        api = auth(client_profile.user)
        payload = api.get("/api/v1/subscriptions/")
        rows = data_of(payload)["results"]
        assert [row["id"] for row in rows] == [minha.id]

    def test_cliente_nao_cancela_assinatura_de_outro(
        self, auth, client_profile, other_client, plan, fake_gateway
    ):
        alheia = subscribe(client=other_client, plan=plan, billing_type=BillingType.PIX_MONTHLY)

        api = auth(client_profile.user)
        response = api.post(f"/api/v1/subscriptions/{alheia.id}/cancel/", {}, format="json")
        # A assinatura de outro cliente nem aparece no queryset do cliente.
        assert response.status_code == 404

        alheia.refresh_from_db()
        assert alheia.status == SubscriptionStatus.PENDING_PAYMENT

    def test_me_devolve_null_sem_assinatura(self, auth, client_profile):
        api = auth(client_profile.user)
        response = api.get("/api/v1/subscriptions/me/")
        assert response.status_code == 200
        assert data_of(response) is None

    def test_me_traz_a_cota_do_ciclo(self, auth, client_profile, plan, service, fake_gateway):
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        confirm_invoice(subscription.invoices.get())

        api = auth(client_profile.user)
        payload = data_of(api.get("/api/v1/subscriptions/me/"))
        quota = payload["quotas"][0]
        assert quota["service"] == service.id
        assert quota["monthly_quota"] == 2
        assert quota["used"] == 0
        assert quota["remaining"] == 2

    def test_cliente_nao_ve_fatura_de_outro(
        self, auth, client_profile, other_client, plan, fake_gateway
    ):
        subscribe(client=other_client, plan=plan, billing_type=BillingType.PIX_MONTHLY)

        api = auth(client_profile.user)
        rows = data_of(api.get("/api/v1/subscription-invoices/"))["results"]
        assert rows == []


class TestConfirmacaoNoCaixa:
    def test_owner_confirma_fatura_manualmente(
        self, auth, owner, client_profile, plan, fake_gateway
    ):
        """Cliente que pagou fora do app precisa ser regularizado no caixa."""
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        invoice = subscription.invoices.get()

        api = auth(owner)
        response = api.post(
            f"/api/v1/subscription-invoices/{invoice.id}/confirm/", {}, format="json"
        )
        assert response.status_code == 200, response.content

        invoice.refresh_from_db()
        subscription.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID
        assert invoice.confirmed_by == owner
        assert subscription.status == SubscriptionStatus.ACTIVE

    def test_cliente_nao_confirma_a_propria_fatura(self, auth, client_profile, plan, fake_gateway):
        """Se o cliente pudesse confirmar, ganharia o plano de graça."""
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        invoice = subscription.invoices.get()

        api = auth(client_profile.user)
        response = api.post(
            f"/api/v1/subscription-invoices/{invoice.id}/confirm/", {}, format="json"
        )
        assert response.status_code == 403

        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PENDING

    def test_fatura_ja_paga_nao_e_confirmada_de_novo(
        self, auth, owner, client_profile, plan, fake_gateway
    ):
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        invoice = subscription.invoices.get()
        confirm_invoice(invoice)

        api = auth(owner)
        response = api.post(
            f"/api/v1/subscription-invoices/{invoice.id}/confirm/", {}, format="json"
        )
        assert response.status_code == 400
        assert "INVOICE_ALREADY_PAID" in str(response.content, "utf-8")


class TestCancelamento:
    def test_cancelar_mantem_o_beneficio_ate_o_fim_do_ciclo(
        self, auth, client_profile, plan, fake_gateway
    ):
        """O cliente pagou o mês: cortar na hora seria tomar o que ele comprou."""
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        confirm_invoice(subscription.invoices.get())

        api = auth(client_profile.user)
        response = api.post(f"/api/v1/subscriptions/{subscription.id}/cancel/", {}, format="json")
        assert response.status_code == 200, response.content

        subscription.refresh_from_db()
        assert subscription.cancel_at_period_end is True
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.grants_benefit is True

    def test_owner_pode_cancelar_imediatamente(
        self, auth, owner, client_profile, plan, fake_gateway
    ):
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        confirm_invoice(subscription.invoices.get())

        api = auth(owner)
        response = api.post(
            f"/api/v1/subscriptions/{subscription.id}/cancel/",
            {"immediate": True},
            format="json",
        )
        assert response.status_code == 200, response.content

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.CANCELLED
        assert subscription.grants_benefit is False

    def test_cliente_nao_forca_cancelamento_imediato(
        self, auth, client_profile, plan, fake_gateway
    ):
        """`immediate` é privilégio do proprietário; do cliente é ignorado."""
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        confirm_invoice(subscription.invoices.get())

        api = auth(client_profile.user)
        api.post(
            f"/api/v1/subscriptions/{subscription.id}/cancel/",
            {"immediate": True},
            format="json",
        )

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.cancel_at_period_end is True

    def test_cancelar_derruba_a_fatura_em_aberto(self, auth, client_profile, plan, fake_gateway):
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )

        api = auth(client_profile.user)
        api.post(f"/api/v1/subscriptions/{subscription.id}/cancel/", {}, format="json")

        assert subscription.invoices.get().status == InvoiceStatus.CANCELLED

    def test_cancelar_libera_para_assinar_outro_plano(
        self, auth, client_profile, plan, unlimited_plan, fake_gateway
    ):
        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )

        api = auth(client_profile.user)
        api.post(f"/api/v1/subscriptions/{subscription.id}/cancel/", {}, format="json")

        response = api.post(
            "/api/v1/subscriptions/subscribe/",
            {"plan": unlimited_plan.id, "billing_type": BillingType.PIX_MONTHLY},
            format="json",
        )
        assert response.status_code == 201, response.content


class TestManutencaoPeriodica:
    def test_renovacao_emite_a_fatura_do_proximo_ciclo(self, client_profile, plan, fake_gateway):
        from apps.plans.services import renew_pix_subscriptions

        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        confirm_invoice(subscription.invoices.get())
        subscription.refresh_from_db()

        # Véspera do fim do ciclo: dentro da janela de antecedência.
        today = subscription.current_period_end
        result = renew_pix_subscriptions(today=today)
        assert result == {"issued": 1}

        proxima = subscription.invoices.get(status=InvoiceStatus.PENDING)
        assert proxima.period_start == subscription.current_period_end + relativedelta(days=1)

    def test_renovacao_nao_duplica_a_fatura(self, client_profile, plan, fake_gateway):
        from apps.plans.services import renew_pix_subscriptions

        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        confirm_invoice(subscription.invoices.get())
        subscription.refresh_from_db()

        today = subscription.current_period_end
        renew_pix_subscriptions(today=today)
        assert renew_pix_subscriptions(today=today) == {"issued": 0}
        assert subscription.invoices.count() == 2

    def test_renovacao_ignora_quem_cancelou(self, client_profile, plan, fake_gateway):
        from apps.plans.services import cancel_subscription, renew_pix_subscriptions

        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        confirm_invoice(subscription.invoices.get())
        subscription.refresh_from_db()
        cancel_subscription(subscription)
        subscription.refresh_from_db()

        assert renew_pix_subscriptions(today=subscription.current_period_end) == {"issued": 0}

    def test_atraso_suspende_apos_a_tolerancia(self, client_profile, plan, fake_gateway):
        from apps.plans.services import suspend_overdue_subscriptions

        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        confirm_invoice(subscription.invoices.get())
        subscription.refresh_from_db()

        # Ainda dentro da tolerância de 3 dias: nada acontece.
        no_prazo = subscription.current_period_end + relativedelta(days=2)
        assert suspend_overdue_subscriptions(today=no_prazo)["past_due"] == 0

        atrasado = subscription.current_period_end + relativedelta(days=10)
        assert suspend_overdue_subscriptions(today=atrasado)["past_due"] == 1

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.PAST_DUE
        assert subscription.grants_benefit is False

    def test_sync_confirma_pix_aprovado_sem_webhook(self, client_profile, plan, fake_gateway):
        """Rede de segurança: se o webhook não chegar, a varredura resolve."""
        from apps.plans.services import sync_pending_invoices

        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        fake_gateway.payment_status = "approved"

        assert sync_pending_invoices() == {"checked": 1, "confirmed": 1}

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.ACTIVE

    def test_sync_expira_pix_recusado(self, client_profile, plan, fake_gateway):
        from apps.plans.services import sync_pending_invoices

        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        fake_gateway.payment_status = "cancelled"

        sync_pending_invoices()
        assert subscription.invoices.get().status == InvoiceStatus.EXPIRED

    def test_qr_vencido_e_marcado_como_expirado(self, client_profile, plan, fake_gateway):
        from apps.plans.services import expire_stale_invoices

        subscription = subscribe(
            client=client_profile, plan=plan, billing_type=BillingType.PIX_MONTHLY
        )
        SubscriptionInvoice.objects.filter(subscription=subscription).update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )

        assert expire_stale_invoices() == 1
        assert subscription.invoices.get().status == InvoiceStatus.EXPIRED


class TestCicloMensal:
    def test_ciclo_nao_estoura_em_mes_curto(self):
        """31/01 + 1 mês deve terminar em 27/02, não em 03/03."""
        import datetime

        start, end = period_bounds(datetime.date(2026, 1, 31))
        assert start == datetime.date(2026, 1, 31)
        assert end == datetime.date(2026, 2, 27)

    def test_ciclo_normal_fecha_no_dia_anterior(self):
        import datetime

        _, end = period_bounds(datetime.date(2026, 3, 10))
        assert end == datetime.date(2026, 4, 9)
