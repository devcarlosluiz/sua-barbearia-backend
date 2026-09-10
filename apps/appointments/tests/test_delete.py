"""Exclusão de agendamentos.

Cancelar e excluir são coisas diferentes: cancelar guarda o registro com o
motivo, excluir apaga. O que se testa aqui é que a exclusão exista para o
lançamento errado — e que ela pare antes de destruir o financeiro.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.appointments.models import Appointment, AppointmentStatus
from apps.appointments.services import booking
from apps.core.exceptions import BusinessError
from apps.core.testing import code_of, data_of, expire_cancellation_window

pytestmark = pytest.mark.django_db


def url(appointment: Appointment) -> str:
    return f"/api/v1/appointments/{appointment.pk}/"



class TestQuemPodeExcluir:
    def test_owner_exclui(self, auth, owner, appointment):
        response = auth(owner).delete(url(appointment))

        assert response.status_code == 204, response.content
        assert not Appointment.objects.filter(pk=appointment.pk).exists()

    def test_barbeiro_nao_exclui(self, auth, barber, appointment):
        """Apagar o próprio horário faria sumir a evidência de uma falta."""
        response = auth(barber.user).delete(url(appointment))

        assert response.status_code == 403
        assert code_of(response) == "PERMISSION_DENIED"
        assert Appointment.objects.filter(pk=appointment.pk).exists()

    def test_cliente_exclui_o_proprio(self, auth, client_profile, appointment):
        response = auth(client_profile.user).delete(url(appointment))

        assert response.status_code == 204, response.content
        assert not Appointment.objects.filter(pk=appointment.pk).exists()

    def test_cliente_nao_exclui_o_de_outro(self, auth, other_client, appointment):
        """O queryset já esconderia, mas a regra tem de valer no service."""
        response = auth(other_client.user).delete(url(appointment))

        assert response.status_code in (403, 404)
        assert Appointment.objects.filter(pk=appointment.pk).exists()

    def test_anonimo_nao_exclui(self, api, appointment):
        assert api.delete(url(appointment)).status_code == 401


class TestPrazoDoCliente:
    """Excluir não pode ser a porta dos fundos do prazo de cancelamento."""

    def test_cliente_nao_exclui_fora_do_prazo(self, auth, client_profile, appointment):
        expire_cancellation_window(appointment)

        response = auth(client_profile.user).delete(url(appointment))

        assert response.status_code == 400
        assert code_of(response) == "CANCELLATION_DEADLINE_PASSED"
        assert Appointment.objects.filter(pk=appointment.pk).exists()

    def test_owner_exclui_fora_do_prazo(self, auth, owner, appointment):
        expire_cancellation_window(appointment)

        assert auth(owner).delete(url(appointment)).status_code == 204

    def test_cliente_nao_exclui_atendimento_em_andamento(
        self, auth, client_profile, appointment
    ):
        appointment.status = AppointmentStatus.IN_PROGRESS
        appointment.save(update_fields=["status"])

        response = auth(client_profile.user).delete(url(appointment))

        assert response.status_code == 400
        assert code_of(response) == "APPOINTMENT_NOT_DELETABLE_BY_CLIENT"

    def test_cliente_nao_apaga_a_propria_falta(self, auth, client_profile, appointment):
        """Senão bastaria faltar e excluir para limpar o histórico."""
        appointment.status = AppointmentStatus.NO_SHOW
        appointment.save(update_fields=["status"])

        response = auth(client_profile.user).delete(url(appointment))

        assert response.status_code == 400
        assert code_of(response) == "APPOINTMENT_NOT_DELETABLE_BY_CLIENT"
        assert Appointment.objects.filter(pk=appointment.pk).exists()


class TestOQuePodeSerExcluido:
    def test_agendamento_cancelado_e_excluido(self, auth, owner, appointment):
        booking.cancel_appointment(appointment=appointment, user=owner)

        response = auth(owner).delete(url(appointment))

        assert response.status_code == 204, response.content
        assert not Appointment.objects.filter(pk=appointment.pk).exists()

    def test_falta_e_excluida(self, auth, owner, appointment):
        appointment.status = AppointmentStatus.NO_SHOW
        appointment.save(update_fields=["status"])

        assert auth(owner).delete(url(appointment)).status_code == 204

    def test_atendimento_concluido_nao_e_excluido(self, auth, owner, appointment):
        """O concluído tem pagamento, comissão e pontos: apagá-lo quebra o mês."""
        booking.complete_appointment(
            appointment=appointment, user=owner, payment_method="CASH"
        )

        response = auth(owner).delete(url(appointment))

        assert response.status_code == 400
        assert code_of(response) == "APPOINTMENT_COMPLETED_CANNOT_DELETE"
        assert Appointment.objects.filter(pk=appointment.pk).exists()

    def test_agendamento_com_pagamento_nao_e_excluido(self, auth, owner, appointment):
        """`Payment` é PROTECT: sem esta checagem o banco devolveria um erro cru."""
        from apps.payments.models import Payment, PaymentMethod, PaymentStatus

        Payment.objects.create(
            branch=appointment.branch,
            appointment=appointment,
            client=appointment.client,
            amount=Decimal("50.00"),
            method=PaymentMethod.PIX,
            status=PaymentStatus.PENDING,
        )

        response = auth(owner).delete(url(appointment))

        assert response.status_code == 400
        assert code_of(response) == "APPOINTMENT_HAS_PAYMENT"
        assert Appointment.objects.filter(pk=appointment.pk).exists()


class TestEfeitos:
    def test_excluir_libera_o_horario(
        self, auth, owner, appointment, branch, barber, service, other_client, next_workday
    ):
        dia, hora = appointment.date, appointment.start_time
        auth(owner).delete(url(appointment))

        novo = booking.create_appointment(
            client=other_client,
            branch_id=branch.id,
            barber_id=barber.id,
            service_id=service.id,
            day=dia,
            start_time=hora,
        )
        assert novo.pk

    def test_excluir_leva_junto_o_historico_de_status(self, auth, owner, appointment):
        from apps.appointments.models import AppointmentStatusHistory

        booking.transition_status(
            appointment=appointment, to_status=AppointmentStatus.ARRIVED, user=owner
        )
        assert AppointmentStatusHistory.objects.filter(appointment=appointment).exists()

        auth(owner).delete(url(appointment))

        assert not AppointmentStatusHistory.objects.filter(
            appointment_id=appointment.pk
        ).exists()

    def test_excluir_nao_afeta_outro_agendamento(
        self, auth, owner, appointment, branch, barber, service, other_client
    ):
        from datetime import time

        outro = booking.create_appointment(
            client=other_client,
            branch_id=branch.id,
            barber_id=barber.id,
            service_id=service.id,
            day=appointment.date,
            start_time=time(15, 0),
        )

        auth(owner).delete(url(appointment))

        assert Appointment.objects.filter(pk=outro.pk).exists()


class TestListaDoCliente:
    """A lista é a tela onde os botões vivem — ela precisa carregar o prazo.

    Enquanto `can_be_cancelled_by_client` só existia no detalhe, o app recebia
    `false` por omissão e escondia cancelar e excluir na área do cliente.
    """

    def test_upcoming_traz_o_prazo(self, auth, client_profile, appointment):
        response = auth(client_profile.user).get("/api/v1/appointments/upcoming/")

        assert response.status_code == 200
        item = data_of(response)[0]
        assert item["can_be_cancelled_by_client"] is True

    def test_upcoming_marca_false_fora_do_prazo(self, auth, client_profile, appointment):
        expire_cancellation_window(appointment)

        response = auth(client_profile.user).get("/api/v1/appointments/upcoming/")

        assert data_of(response)[0]["can_be_cancelled_by_client"] is False

    def test_agenda_do_dia_tambem_traz(self, auth, owner, appointment):
        response = auth(owner).get(
            "/api/v1/appointments/agenda/", {"date": appointment.date.isoformat()}
        )

        assert response.status_code == 200
        assert "can_be_cancelled_by_client" in data_of(response)["appointments"][0]


class TestServico:
    def test_servico_recusa_concluido(self, appointment, owner):
        appointment.status = AppointmentStatus.COMPLETED
        appointment.save(update_fields=["status"])

        with pytest.raises(BusinessError) as exc:
            booking.delete_appointment(appointment=appointment, user=owner)
        assert exc.value.business_code == "APPOINTMENT_COMPLETED_CANNOT_DELETE"
