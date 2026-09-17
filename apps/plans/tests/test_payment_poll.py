"""A consulta ao provedor logo depois de emitir o QR.

O webhook é o caminho principal, mas ele não chega em desenvolvimento e pode
atrasar em produção — e o cliente, que acabou de pagar, fica olhando para um
plano inativo. Estes testes protegem o outro caminho: perguntar ao Asaas
de poucos em poucos segundos enquanto a cobrança recém-emitida segue em aberto.
"""

from __future__ import annotations

import pytest

from apps.plans.models import InvoiceStatus, SubscriptionStatus
from apps.plans.services import subscribe
from apps.plans.tasks import poll_pix_invoice

pytestmark = pytest.mark.django_db


@pytest.fixture
def polling(settings):
    """Liga a consulta, que a suíte mantém desligada por causa do modo eager."""
    settings.SUBSCRIPTION_SETTINGS = {
        **settings.SUBSCRIPTION_SETTINGS,
        "PIX_POLL_INTERVAL_SECONDS": 15,
        "PIX_POLL_ATTEMPTS": 3,
    }
    return settings


@pytest.fixture
def invoice(client_profile, plan, fake_gateway):
    subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
    return subscription.invoices.get()


class TestConsultaDaFatura:
    def test_pix_pago_ativa_a_assinatura(self, polling, invoice, fake_gateway):
        fake_gateway.payment_status = "CONFIRMED"

        result = poll_pix_invoice(invoice.pk)

        assert result["outcome"] == "confirmed"
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID
        invoice.subscription.refresh_from_db()
        assert invoice.subscription.status == SubscriptionStatus.ACTIVE
        assert invoice.subscription.grants_benefit is True

    def test_pix_em_aberto_agenda_a_proxima_consulta(self, polling, invoice, fake_gateway):
        fake_gateway.payment_status = "PENDING"

        result = poll_pix_invoice(invoice.pk, 1)

        # Em modo eager a reagendamento executa na hora, então a cadeia inteira
        # roda: três tentativas e desiste.
        assert result["outcome"] == "pending"
        assert len(fake_gateway.called("get_payment")) == 3
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PENDING

    def test_para_na_ultima_tentativa(self, polling, invoice, fake_gateway):
        fake_gateway.payment_status = "PENDING"

        result = poll_pix_invoice(invoice.pk, 3)

        assert result["outcome"] == "gave_up"
        assert len(fake_gateway.called("get_payment")) == 1

    def test_pix_recusado_encerra_a_cobranca(self, polling, invoice, fake_gateway):
        fake_gateway.payment_status = "OVERDUE"

        assert poll_pix_invoice(invoice.pk)["outcome"] == "expired"
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.EXPIRED

    def test_fatura_ja_confirmada_pelo_webhook_nao_e_consultada(
        self, polling, invoice, fake_gateway
    ):
        """O webhook chegou primeiro: a consulta não tem mais o que fazer."""
        from apps.plans.services import confirm_invoice

        confirm_invoice(invoice)
        fake_gateway.calls.clear()

        assert poll_pix_invoice(invoice.pk)["outcome"] == "skipped"
        assert fake_gateway.called("get_payment") == []

    def test_fatura_inexistente_nao_quebra(self, polling):
        assert poll_pix_invoice(999999)["outcome"] == "not_found"


class TestAgendamentoNaEmissao:
    def test_emitir_o_qr_dispara_a_consulta(
        self, polling, django_capture_on_commit_callbacks, client_profile, plan, fake_gateway
    ):
        """Sem isto, nada acompanha o pagamento até a varredura de 20 minutos."""
        fake_gateway.payment_status = "CONFIRMED"

        with django_capture_on_commit_callbacks(execute=True):
            subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        # A consulta é enfileirada no commit — antes dele o worker procuraria
        # uma fatura que ainda não existe para ele.
        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.ACTIVE

    def test_zero_tentativas_desliga_a_consulta(
        self, settings, django_capture_on_commit_callbacks, client_profile, plan, fake_gateway
    ):
        settings.SUBSCRIPTION_SETTINGS = {
            **settings.SUBSCRIPTION_SETTINGS,
            "PIX_POLL_ATTEMPTS": 0,
        }
        fake_gateway.payment_status = "CONFIRMED"

        with django_capture_on_commit_callbacks(execute=True):
            subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        subscription.refresh_from_db()
        assert subscription.status == SubscriptionStatus.PENDING_PAYMENT
        assert fake_gateway.called("get_payment") == []
