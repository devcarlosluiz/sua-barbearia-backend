"""O plano visto de dentro do atendimento.

O benefício já era aplicado na finalização, mas só lá: a agenda do barbeiro
mostrava o preço cheio e mandava cobrar um cliente que já paga mensalidade.
Estes testes protegem o campo `plan_coverage`, que responde antes do
atendimento a única pergunta que o barbeiro faz — cobro ou não cobro?
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.appointments.models import Appointment, AppointmentStatus
from apps.appointments.services.booking import complete_appointment
from apps.core.testing import data_of
from apps.payments.models import PaymentMethod
from apps.plans.models import SubscriptionStatus
from apps.plans.services import appointment_coverage_map, confirm_invoice, subscribe

pytestmark = pytest.mark.django_db


@pytest.fixture
def subscriber(client_profile, plan, fake_gateway):
    """Cliente com assinatura ativa e ciclo em curso."""
    subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
    confirm_invoice(subscription.invoices.get())
    subscription.refresh_from_db()
    return subscription


def make_appointment(
    branch, barber, service, client_profile, *, day_offset: int = 0, status=None, hour: int = 9
):
    day = timezone.localdate() + timezone.timedelta(days=day_offset)
    return Appointment.objects.create(
        branch=branch,
        barber=barber,
        service=service,
        client=client_profile,
        date=day,
        start_time=time(hour, 0),
        end_time=time(hour, 30),
        price=service.price,
        status=status or AppointmentStatus.IN_PROGRESS,
    )


def coverage_of(appointment):
    return appointment_coverage_map([appointment])[appointment.pk]


class TestCotaDisponivel:
    def test_diz_ao_barbeiro_para_nao_cobrar(
        self, subscriber, branch, barber, service, client_profile
    ):
        appointment = make_appointment(branch, barber, service, client_profile)

        coverage = coverage_of(appointment)

        assert coverage["is_subscriber"] is True
        assert coverage["is_covered"] is True
        assert coverage["charge_client"] is False
        assert coverage["amount_due"] == "0.00"
        assert coverage["plan_name"] == subscriber.plan.name
        assert coverage["remaining"] == 2

    def test_uso_ja_consumido_desconta_da_cota_restante(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        complete_appointment(
            appointment=make_appointment(branch, barber, service, client_profile),
            user=owner,
            payment_method=PaymentMethod.PIX,
        )

        proximo = make_appointment(branch, barber, service, client_profile, day_offset=1)
        assert coverage_of(proximo)["remaining"] == 1

    def test_plano_ilimitado_nao_tem_cota_restante(
        self, client_profile, unlimited_plan, branch, barber, service, fake_gateway
    ):
        subscription = subscribe(
            client=client_profile, plan=unlimited_plan, billing_type="PIX_MONTHLY"
        )
        confirm_invoice(subscription.invoices.get())

        coverage = coverage_of(make_appointment(branch, barber, service, client_profile))

        assert coverage["is_covered"] is True
        assert coverage["remaining"] is None


class TestCotaEsgotada:
    def test_cai_no_desconto_do_plano(
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

        coverage = coverage_of(
            make_appointment(branch, barber, service, client_profile, day_offset=2)
        )

        assert coverage["is_covered"] is False
        assert coverage["charge_client"] is True
        assert coverage["remaining"] == 0
        assert coverage["discount_percentage"] == "50.00"
        # O barbeiro cobra o valor com desconto, não o preço cheio.
        assert coverage["amount_due"] == "25.00"


class TestSemBeneficio:
    def test_cliente_sem_plano_nao_tem_campo(self, branch, barber, service, client_profile):
        assert coverage_of(make_appointment(branch, barber, service, client_profile)) is None

    def test_assinatura_atrasada_manda_cobrar(
        self, subscriber, branch, barber, service, client_profile
    ):
        subscriber.status = SubscriptionStatus.PAST_DUE
        subscriber.save(update_fields=["status"])

        coverage = coverage_of(make_appointment(branch, barber, service, client_profile))

        assert coverage["is_covered"] is False
        assert coverage["charge_client"] is True
        assert coverage["amount_due"] == "50.00"
        assert coverage["subscription_status"] == SubscriptionStatus.PAST_DUE

    def test_servico_fora_do_plano_manda_cobrar(self, subscriber, branch, barber, client_profile):
        from apps.services.models import Service

        fora = Service.objects.create(name="Pigmentação", duration_minutes=45, price=Decimal("80"))

        coverage = coverage_of(make_appointment(branch, barber, fora, client_profile))

        assert coverage["covers_service"] is False
        assert coverage["amount_due"] == "80.00"

    def test_filial_fora_do_plano_manda_cobrar(self, subscriber, barber, service, client_profile):
        from apps.branches.models import Branch

        outra = Branch.objects.create(name="Unidade 2", city="Curitiba", state="PR")
        barber.branches.add(outra)

        coverage = coverage_of(make_appointment(outra, barber, service, client_profile))

        assert coverage["is_covered"] is False
        assert coverage["charge_client"] is True


class TestAtendimentoEncerrado:
    def test_concluido_com_cota_segue_marcado_como_coberto(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        appointment = make_appointment(branch, barber, service, client_profile)
        complete_appointment(appointment=appointment, user=owner, payment_method=PaymentMethod.PIX)

        coverage = coverage_of(appointment)

        assert coverage["is_covered"] is True
        assert coverage["amount_due"] == "0.00"

    def test_concluido_sem_cota_nao_reavalia_o_plano(
        self, subscriber, branch, barber, service, client_profile, owner
    ):
        """A cota de hoje não pode reescrever o que já foi pago ontem."""
        appointment = make_appointment(branch, barber, service, client_profile)
        complete_appointment(
            appointment=appointment,
            user=owner,
            payment_method=PaymentMethod.CASH,
            discount_amount=Decimal("5.00"),
        )

        assert coverage_of(appointment) is None


class TestNaAgendaDoBarbeiro:
    def test_agenda_entrega_a_cobertura_de_cada_atendimento(
        self, auth, barber, subscriber, branch, service, client_profile, other_client
    ):
        coberto = make_appointment(branch, barber, service, client_profile, hour=9)
        sem_plano = make_appointment(branch, barber, service, other_client, hour=10)

        api = auth(barber.user)
        response = api.get(f"/api/v1/appointments/agenda/?date={coberto.date.isoformat()}")
        assert response.status_code == 200, response.content

        por_id = {row["id"]: row for row in data_of(response)["appointments"]}
        assert por_id[coberto.pk]["plan_coverage"]["charge_client"] is False
        assert por_id[sem_plano.pk]["plan_coverage"] is None

    def test_agenda_nao_consulta_o_plano_por_linha(
        self,
        auth,
        django_assert_max_num_queries,
        barber,
        subscriber,
        branch,
        service,
        client_profile,
        other_client,
    ):
        """Uma agenda cheia não pode custar três consultas por atendimento."""
        day = timezone.localdate()
        for hour in range(9, 15):
            make_appointment(branch, barber, service, client_profile, hour=hour)

        api = auth(barber.user)
        with django_assert_max_num_queries(10):
            response = api.get(f"/api/v1/appointments/agenda/?date={day.isoformat()}")
        assert response.status_code == 200, response.content
