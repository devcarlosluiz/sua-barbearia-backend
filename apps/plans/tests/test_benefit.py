"""O plano aplicado no atendimento.

É aqui que o plano deixa de ser uma vitrine e passa a mexer em dinheiro:
quanto o cliente paga, o que entra no caixa, quanto o barbeiro recebe e quanto
da cota sobra. Cada asserção abaixo protege um desses números.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.appointments.models import Appointment, AppointmentStatus
from apps.appointments.services.booking import complete_appointment
from apps.finance.models import Commission, Transaction, TransactionCategory
from apps.payments.models import Payment, PaymentMethod
from apps.plans.models import SubscriptionUsage
from apps.plans.services import confirm_invoice, coverage_for, subscribe

pytestmark = pytest.mark.django_db


@pytest.fixture
def subscriber(client_profile, plan, fake_gateway):
    """Cliente com assinatura ativa e ciclo em curso."""
    subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
    confirm_invoice(subscription.invoices.get())
    subscription.refresh_from_db()
    return subscription


def make_appointment(branch, barber, service, client_profile, *, day_offset: int = 0):
    """Atendimento pronto para ser finalizado."""
    from datetime import time

    day = timezone.localdate() + timezone.timedelta(days=day_offset)
    return Appointment.objects.create(
        branch=branch,
        barber=barber,
        service=service,
        client=client_profile,
        date=day,
        start_time=time(9, 0),
        end_time=time(9, 30),
        price=service.price,
        status=AppointmentStatus.IN_PROGRESS,
    )


class TestCotaDisponivel:
    def test_atendimento_coberto_nao_cobra_o_cliente(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        appointment = make_appointment(branch, barber, service, client_profile)

        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        payment = Payment.objects.get(appointment=appointment)
        assert payment.net_amount == Decimal("0.00")
        assert payment.discount_amount == service.price
        # A forma de pagamento passa a registrar a origem real do dinheiro.
        assert payment.method == PaymentMethod.SUBSCRIPTION

    def test_nao_lanca_receita_duplicada_no_caixa(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        """A mensalidade já entrou; cobrar o corte de novo inflaria o caixa."""
        appointment = make_appointment(branch, barber, service, client_profile)

        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        assert not Transaction.objects.filter(appointment=appointment).exists()
        # A única receita é a da assinatura.
        assert Transaction.objects.filter(category=TransactionCategory.SUBSCRIPTION).count() == 1

    def test_barbeiro_recebe_comissao_sobre_o_preco_do_servico(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        """O barbeiro trabalhou: cortar a comissão a zero seria injusto."""
        appointment = make_appointment(branch, barber, service, client_profile)

        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        commission = Commission.objects.get(appointment=appointment)
        assert commission.base_amount == service.price
        assert commission.amount == (
            service.price * barber.commission_percentage / Decimal("100")
        ).quantize(Decimal("0.01"))

    def test_plano_pode_nao_pagar_comissao(
        self, subscriber, branch, barber, service, client_profile, owner, plan
    ):
        """A decisão é do proprietário, não nossa."""
        plan.pays_barber_commission = False
        plan.save(update_fields=["pays_barber_commission"])

        appointment = make_appointment(branch, barber, service, client_profile)
        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        assert not Commission.objects.filter(appointment=appointment).exists()

    def test_uso_consome_a_cota(self, subscriber, branch, barber, service, client_profile, owner):
        appointment = make_appointment(branch, barber, service, client_profile)

        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        usage = SubscriptionUsage.objects.get(appointment=appointment)
        assert usage.subscription == subscriber
        assert usage.service == service
        assert usage.covered_amount == service.price
        assert usage.period_start == subscriber.current_period_start

        coverage = coverage_for(client_profile, service, branch_id=branch.id)
        assert coverage.remaining == 1


class TestCotaEsgotada:
    def test_terceiro_uso_cai_no_desconto_do_plano(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        """Cota de 2 no plano, desconto de 50% no que passar."""
        for offset in (0, 1):
            complete_appointment(
                appointment=make_appointment(
                    branch, barber, service, client_profile, day_offset=offset
                ),
                user=owner,
                payment_method=PaymentMethod.PIX,
            )

        terceiro = make_appointment(branch, barber, service, client_profile, day_offset=2)
        complete_appointment(appointment=terceiro, user=owner, payment_method=PaymentMethod.PIX)

        payment = Payment.objects.get(appointment=terceiro)
        esperado = (service.price / 2).quantize(Decimal("0.01"))
        assert payment.discount_amount == esperado
        assert payment.net_amount == service.price - esperado
        # Não é uso de cota: é venda com desconto, e entra no caixa.
        assert payment.method == PaymentMethod.PIX
        assert Transaction.objects.filter(appointment=terceiro).exists()

    def test_uso_acima_da_cota_nao_registra_consumo(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        for offset in (0, 1, 2):
            complete_appointment(
                appointment=make_appointment(
                    branch, barber, service, client_profile, day_offset=offset
                ),
                user=owner,
                payment_method=PaymentMethod.PIX,
            )

        assert SubscriptionUsage.objects.count() == 2

    def test_plano_sem_desconto_cobra_o_valor_cheio(
        self, subscriber, branch, barber, service, client_profile, owner, plan
    ):
        plan.overage_discount_percentage = Decimal("0.00")
        plan.save(update_fields=["overage_discount_percentage"])

        for offset in (0, 1):
            complete_appointment(
                appointment=make_appointment(
                    branch, barber, service, client_profile, day_offset=offset
                ),
                user=owner,
                payment_method=PaymentMethod.PIX,
            )

        terceiro = make_appointment(branch, barber, service, client_profile, day_offset=2)
        complete_appointment(appointment=terceiro, user=owner, payment_method=PaymentMethod.PIX)

        payment = Payment.objects.get(appointment=terceiro)
        assert payment.discount_amount == Decimal("0.00")
        assert payment.net_amount == service.price


class TestQuandoOPlanoNaoVale:
    def test_sem_assinatura_o_preco_e_normal(self, branch, barber, service, client_profile, owner):
        appointment = make_appointment(branch, barber, service, client_profile)
        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        payment = Payment.objects.get(appointment=appointment)
        assert payment.net_amount == service.price
        assert payment.method == PaymentMethod.PIX
        assert not SubscriptionUsage.objects.exists()

    def test_assinatura_pendente_nao_da_beneficio(
        self, client_profile, plan, branch, barber, service, owner, fake_gateway
    ):
        """Um PIX emitido e não pago não pode virar corte de graça."""
        subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        appointment = make_appointment(branch, barber, service, client_profile)
        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        assert Payment.objects.get(appointment=appointment).net_amount == service.price

    def test_assinatura_suspensa_nao_da_beneficio(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        from apps.plans.models import SubscriptionStatus

        subscriber.status = SubscriptionStatus.PAST_DUE
        subscriber.save(update_fields=["status"])

        appointment = make_appointment(branch, barber, service, client_profile)
        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        assert Payment.objects.get(appointment=appointment).net_amount == service.price

    def test_ciclo_vencido_nao_da_beneficio(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        subscriber.current_period_end = timezone.localdate() - timezone.timedelta(days=1)
        subscriber.save(update_fields=["current_period_end"])

        appointment = make_appointment(branch, barber, service, client_profile)
        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        assert Payment.objects.get(appointment=appointment).net_amount == service.price

    def test_servico_fora_do_plano_nao_e_coberto(
        self, subscriber, branch, barber, client_profile, owner
    ):
        from apps.services.models import Service

        fora = Service.objects.create(
            name="Pigmentação", duration_minutes=45, price=Decimal("80.00")
        )
        appointment = make_appointment(branch, barber, fora, client_profile)
        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        assert Payment.objects.get(appointment=appointment).net_amount == Decimal("80.00")

    def test_filial_fora_do_plano_nao_e_coberta(
        self, subscriber, barber, service, client_profile, owner
    ):
        """O plano vale só na filial contratada."""
        from apps.branches.models import Branch

        outra = Branch.objects.create(name="Unidade 2", city="Curitiba", state="PR")
        barber.branches.add(outra)

        appointment = make_appointment(outra, barber, service, client_profile)
        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        assert Payment.objects.get(appointment=appointment).net_amount == service.price


class TestPrecedencia:
    def test_desconto_manual_do_balcao_tem_prioridade(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        """Se o operador digitou um desconto, é decisão dele — não sobrepomos.

        E, como não houve uso de cota, o plano não é consumido.
        """
        appointment = make_appointment(branch, barber, service, client_profile)
        complete_appointment(
            appointment=appointment,
            user=owner,
            payment_method=PaymentMethod.CASH,
            discount_amount=Decimal("5.00"),
        )

        payment = Payment.objects.get(appointment=appointment)
        assert payment.discount_amount == Decimal("5.00")
        assert payment.method == PaymentMethod.CASH
        assert not SubscriptionUsage.objects.exists()

    def test_plano_ilimitado_cobre_sempre(
        self, client_profile, unlimited_plan, branch, barber, service, owner, fake_gateway
    ):
        subscription = subscribe(
            client=client_profile, plan=unlimited_plan, billing_type="PIX_MONTHLY"
        )
        confirm_invoice(subscription.invoices.get())

        for offset in range(4):
            appointment = make_appointment(
                branch, barber, service, client_profile, day_offset=offset
            )
            complete_appointment(
                appointment=appointment, user=owner, payment_method=PaymentMethod.PIX
            )
            assert Payment.objects.get(appointment=appointment).net_amount == Decimal("0.00")

        assert SubscriptionUsage.objects.count() == 4

    def test_finalizar_duas_vezes_nao_consome_duas_cotas(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        from apps.core.exceptions import BusinessError

        appointment = make_appointment(branch, barber, service, client_profile)
        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        # A máquina de estados já barra a segunda finalização...
        with pytest.raises(BusinessError):
            complete_appointment(
                appointment=appointment, user=owner, payment_method=PaymentMethod.PIX
            )
        # ...e o registro de uso é único por atendimento de qualquer forma.
        assert SubscriptionUsage.objects.filter(appointment=appointment).count() == 1
