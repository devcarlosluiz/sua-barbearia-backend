"""Testes do fluxo de agendamento: criação, cancelamento, reagendamento e atendimento."""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.appointments.models import Appointment, AppointmentStatus, CancelledBy
from apps.appointments.services import booking
from apps.core.exceptions import BusinessError, ConflictError
from apps.core.testing import code_of, data_of, expire_cancellation_window
from apps.finance.models import Commission, CommissionStatus, Transaction, TransactionType
from apps.payments.models import Payment, PaymentStatus

pytestmark = pytest.mark.django_db



class TestCriacao:
    def test_cria_agendamento_valido(self, branch, barber, service, client_profile, next_workday):
        appointment = booking.create_appointment(
            client=client_profile,
            branch_id=branch.id,
            barber_id=barber.id,
            service_id=service.id,
            day=next_workday,
            start_time=time(10, 0),
        )
        assert appointment.status == AppointmentStatus.PENDING
        assert appointment.end_time == time(10, 30)
        assert appointment.price == service.price
        assert appointment.status_history.count() == 1

    def test_conflito_de_horario_e_rejeitado(
        self, branch, barber, service, client_profile, other_client, next_workday
    ):
        booking.create_appointment(
            client=client_profile,
            branch_id=branch.id,
            barber_id=barber.id,
            service_id=service.id,
            day=next_workday,
            start_time=time(10, 0),
        )
        with pytest.raises(ConflictError) as exc:
            booking.create_appointment(
                client=other_client,
                branch_id=branch.id,
                barber_id=barber.id,
                service_id=service.id,
                day=next_workday,
                start_time=time(10, 0),
            )
        assert exc.value.business_code == "SLOT_NOT_AVAILABLE"

    def test_sobreposicao_parcial_e_rejeitada(
        self, branch, barber, service, client_profile, other_client, next_workday
    ):
        service.duration_minutes = 60
        service.save(update_fields=["duration_minutes"])
        booking.create_appointment(
            client=client_profile,
            branch_id=branch.id,
            barber_id=barber.id,
            service_id=service.id,
            day=next_workday,
            start_time=time(10, 0),
        )
        with pytest.raises(ConflictError):
            booking.create_appointment(
                client=other_client,
                branch_id=branch.id,
                barber_id=barber.id,
                service_id=service.id,
                day=next_workday,
                start_time=time(10, 30),
            )

    def test_horario_fora_do_expediente_e_rejeitado(
        self, branch, barber, service, client_profile, next_workday
    ):
        with pytest.raises(ConflictError) as exc:
            booking.create_appointment(
                client=client_profile,
                branch_id=branch.id,
                barber_id=barber.id,
                service_id=service.id,
                day=next_workday,
                start_time=time(20, 0),
            )
        assert exc.value.business_code == "SLOT_NOT_AVAILABLE"

    def test_horario_no_almoco_e_rejeitado(
        self, branch, barber, service, client_profile, next_workday
    ):
        with pytest.raises(ConflictError):
            booking.create_appointment(
                client=client_profile,
                branch_id=branch.id,
                barber_id=barber.id,
                service_id=service.id,
                day=next_workday,
                start_time=time(12, 0),
            )

    def test_barbeiro_indisponivel_e_rejeitado(
        self, branch, barber, service, client_profile, next_workday
    ):
        from apps.barbers.models import TimeOff
        from apps.core.utils import combine_local

        TimeOff.objects.create(
            barber=barber,
            starts_at=combine_local(next_workday, time(9, 0)),
            ends_at=combine_local(next_workday, time(18, 0)),
            reason="Folga",
        )
        with pytest.raises(ConflictError):
            booking.create_appointment(
                client=client_profile,
                branch_id=branch.id,
                barber_id=barber.id,
                service_id=service.id,
                day=next_workday,
                start_time=time(10, 0),
            )

    def test_criacao_pela_equipe_ja_confirma(
        self, branch, barber, service, client_profile, next_workday, owner
    ):
        appointment = booking.create_appointment(
            client=client_profile,
            branch_id=branch.id,
            barber_id=barber.id,
            service_id=service.id,
            day=next_workday,
            start_time=time(11, 0),
            created_by=owner,
            auto_confirm=True,
        )
        assert appointment.status == AppointmentStatus.CONFIRMED
        assert appointment.confirmed_at is not None


class TestCancelamento:
    def test_cliente_cancela_dentro_do_prazo(self, appointment, client_profile):
        result = booking.cancel_appointment(
            appointment=appointment, user=client_profile.user, reason="Imprevisto"
        )
        assert result.status == AppointmentStatus.CANCELLED
        assert result.cancelled_by_role == CancelledBy.CLIENT
        assert result.cancellation_reason == "Imprevisto"
        assert result.cancelled_at is not None

    def test_cliente_nao_cancela_fora_do_prazo(self, appointment, client_profile):
        expire_cancellation_window(appointment)

        with pytest.raises(BusinessError) as exc:
            booking.cancel_appointment(appointment=appointment, user=client_profile.user)
        assert exc.value.business_code == "CANCELLATION_DEADLINE_PASSED"

    def test_owner_cancela_a_qualquer_momento(self, appointment, owner):
        expire_cancellation_window(appointment)

        result = booking.cancel_appointment(appointment=appointment, user=owner)
        assert result.status == AppointmentStatus.CANCELLED
        assert result.cancelled_by_role == CancelledBy.OWNER

    def test_cliente_nao_cancela_agendamento_de_outro(self, appointment, other_client):
        with pytest.raises(BusinessError) as exc:
            booking.cancel_appointment(appointment=appointment, user=other_client.user)
        assert exc.value.business_code == "PERMISSION_DENIED"

    def test_agendamento_finalizado_nao_e_cancelado(self, appointment, owner):
        appointment.status = AppointmentStatus.COMPLETED
        appointment.save(update_fields=["status"])
        with pytest.raises(BusinessError) as exc:
            booking.cancel_appointment(appointment=appointment, user=owner)
        assert exc.value.business_code == "APPOINTMENT_ALREADY_FINISHED"

    def test_cancelamento_libera_o_horario(
        self, appointment, branch, barber, service, owner, other_client
    ):
        booking.cancel_appointment(appointment=appointment, user=owner)
        novo = booking.create_appointment(
            client=other_client,
            branch_id=branch.id,
            barber_id=barber.id,
            service_id=service.id,
            day=appointment.date,
            start_time=appointment.start_time,
        )
        assert novo.pk != appointment.pk


class TestReagendamento:
    def test_reagenda_para_novo_horario(self, appointment, client_profile):
        result = booking.reschedule_appointment(
            appointment=appointment,
            day=appointment.date,
            start_time=time(15, 0),
            user=client_profile.user,
        )
        assert result.start_time == time(15, 0)
        assert result.end_time == time(15, 30)

    def test_reagendamento_registra_historico(self, appointment, client_profile):
        original = appointment.start_time
        booking.reschedule_appointment(
            appointment=appointment,
            day=appointment.date,
            start_time=time(16, 0),
            user=client_profile.user,
        )
        history = appointment.status_history.order_by("-created_at").first()
        assert history.metadata["previous"]["start_time"] == original.strftime("%H:%M")
        assert history.metadata["new"]["start_time"] == "16:00"

    def test_reagendamento_para_horario_ocupado_falha(
        self, appointment, branch, barber, service, other_client, client_profile
    ):
        booking.create_appointment(
            client=other_client,
            branch_id=branch.id,
            barber_id=barber.id,
            service_id=service.id,
            day=appointment.date,
            start_time=time(15, 0),
        )
        with pytest.raises(ConflictError):
            booking.reschedule_appointment(
                appointment=appointment,
                day=appointment.date,
                start_time=time(15, 0),
                user=client_profile.user,
            )

    def test_atendimento_concluido_nao_reagenda(self, appointment, owner):
        appointment.status = AppointmentStatus.COMPLETED
        appointment.save(update_fields=["status"])
        with pytest.raises(BusinessError) as exc:
            booking.reschedule_appointment(
                appointment=appointment,
                day=appointment.date,
                start_time=time(15, 0),
                user=owner,
            )
        assert exc.value.business_code == "APPOINTMENT_ALREADY_FINISHED"


class TestMaquinaDeEstados:
    def test_fluxo_completo_de_atendimento(self, appointment, barber):
        booking.transition_status(
            appointment=appointment, to_status=AppointmentStatus.ARRIVED, user=barber.user
        )
        assert appointment.arrived_at is not None

        booking.transition_status(
            appointment=appointment, to_status=AppointmentStatus.IN_PROGRESS, user=barber.user
        )
        assert appointment.started_at is not None
        assert appointment.status == AppointmentStatus.IN_PROGRESS

    def test_transicao_invalida_e_rejeitada(self, appointment, barber):
        with pytest.raises(BusinessError) as exc:
            booking.transition_status(
                appointment=appointment,
                to_status=AppointmentStatus.COMPLETED,
                user=barber.user,
            )
        assert exc.value.business_code == "INVALID_STATUS_TRANSITION"

    def test_status_final_nao_transiciona(self, appointment, barber):
        appointment.status = AppointmentStatus.NO_SHOW
        appointment.save(update_fields=["status"])
        with pytest.raises(BusinessError):
            booking.transition_status(
                appointment=appointment,
                to_status=AppointmentStatus.CONFIRMED,
                user=barber.user,
            )


class TestConclusao:
    def test_conclusao_gera_pagamento_caixa_comissao_e_pontos(
        self, appointment, barber, client_profile
    ):
        booking.transition_status(
            appointment=appointment, to_status=AppointmentStatus.IN_PROGRESS, user=barber.user
        )
        result = booking.complete_appointment(
            appointment=appointment, user=barber.user, payment_method="PIX"
        )

        assert result.status == AppointmentStatus.COMPLETED
        assert result.completed_at is not None

        payment = Payment.objects.get(appointment=appointment)
        assert payment.status == PaymentStatus.PAID
        assert payment.amount == appointment.price

        income = Transaction.objects.get(appointment=appointment, type=TransactionType.INCOME)
        assert income.amount == appointment.price

        commission = Commission.objects.get(appointment=appointment)
        assert commission.status == CommissionStatus.PENDING
        assert commission.amount == (appointment.price * Decimal("0.40")).quantize(Decimal("0.01"))

        client_profile.refresh_from_db()
        assert client_profile.total_visits == 1
        assert client_profile.total_spent == appointment.price
        assert client_profile.loyalty_points == int(appointment.price)

    def test_conclusao_com_desconto(self, appointment, barber, client_profile):
        booking.transition_status(
            appointment=appointment, to_status=AppointmentStatus.IN_PROGRESS, user=barber.user
        )
        booking.complete_appointment(
            appointment=appointment,
            user=barber.user,
            payment_method="CASH",
            discount_amount=Decimal("10.00"),
        )
        payment = Payment.objects.get(appointment=appointment)
        assert payment.discount_amount == Decimal("10.00")
        assert payment.net_amount == appointment.price - Decimal("10.00")

        client_profile.refresh_from_db()
        assert client_profile.total_spent == appointment.price - Decimal("10.00")

    def test_desconto_maior_que_o_valor_e_rejeitado(self, appointment, barber):
        booking.transition_status(
            appointment=appointment, to_status=AppointmentStatus.IN_PROGRESS, user=barber.user
        )
        with pytest.raises(BusinessError) as exc:
            booking.complete_appointment(
                appointment=appointment,
                user=barber.user,
                payment_method="CASH",
                discount_amount=Decimal("999.00"),
            )
        assert exc.value.business_code == "DISCOUNT_GREATER_THAN_AMOUNT"

    def test_nao_conclui_agendamento_pendente(self, appointment, barber):
        appointment.status = AppointmentStatus.PENDING
        appointment.save(update_fields=["status"])
        booking.transition_status(
            appointment=appointment, to_status=AppointmentStatus.CANCELLED, user=barber.user
        )
        with pytest.raises(BusinessError) as exc:
            booking.complete_appointment(
                appointment=appointment, user=barber.user, payment_method="PIX"
            )
        assert exc.value.business_code == "APPOINTMENT_NOT_STARTED"

    def test_cancelamento_cancela_a_comissao(self, appointment, barber, owner):
        booking.transition_status(
            appointment=appointment, to_status=AppointmentStatus.IN_PROGRESS, user=barber.user
        )
        booking.complete_appointment(
            appointment=appointment, user=barber.user, payment_method="PIX"
        )
        appointment.status = AppointmentStatus.CONFIRMED  # reabre para o teste
        appointment.save(update_fields=["status"])

        booking.cancel_appointment(appointment=appointment, user=owner, reason="Erro de caixa")
        commission = Commission.objects.get(appointment=appointment)
        assert commission.status == CommissionStatus.CANCELLED


class TestApiAgendamentos:
    def test_cliente_cria_agendamento_pela_api(
        self, auth, client_profile, branch, barber, service, next_workday
    ):
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/appointments/",
            {
                "branch_id": branch.id,
                "barber_id": barber.id,
                "service_id": service.id,
                "date": next_workday.isoformat(),
                "start_time": "10:00",
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        assert data_of(response)["status"] == AppointmentStatus.PENDING

    def test_cliente_nao_agenda_para_outro_cliente(
        self, auth, client_profile, other_client, branch, barber, service, next_workday
    ):
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/appointments/",
            {
                "branch_id": branch.id,
                "barber_id": barber.id,
                "service_id": service.id,
                "client_id": other_client.id,
                "date": next_workday.isoformat(),
                "start_time": "10:00",
            },
            format="json",
        )
        assert response.status_code == 201
        assert Appointment.objects.get(pk=data_of(response)["id"]).client_id == client_profile.id

    def test_conflito_retorna_409(
        self, auth, client_profile, other_client, branch, barber, service, next_workday
    ):
        payload = {
            "branch_id": branch.id,
            "barber_id": barber.id,
            "service_id": service.id,
            "date": next_workday.isoformat(),
            "start_time": "10:00",
        }
        auth(client_profile.user).post("/api/v1/appointments/", payload, format="json")

        from rest_framework.test import APIClient

        second = APIClient()
        login = second.post(
            "/api/v1/auth/login/",
            {"email": other_client.user.email, "password": "SenhaTeste@2026"},
            format="json",
        )
        second.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['data']['access']}")
        response = second.post("/api/v1/appointments/", payload, format="json")
        assert response.status_code == 409
        assert code_of(response) == "SLOT_NOT_AVAILABLE"

    def test_endpoint_de_slots(self, auth, client_profile, branch, barber, service, next_workday):
        api = auth(client_profile.user)
        response = api.get(
            "/api/v1/appointments/available-slots/",
            {
                "branch_id": branch.id,
                "barber_id": barber.id,
                "service_id": service.id,
                "date": next_workday.isoformat(),
            },
        )
        assert response.status_code == 200
        assert "09:00" in data_of(response)["slots"]
