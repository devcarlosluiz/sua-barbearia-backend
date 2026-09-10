"""Testes do cálculo de disponibilidade."""

from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.utils import timezone

from apps.appointments.models import Appointment, AppointmentStatus
from apps.appointments.services.availability import build_availability
from apps.barbers.models import SpecialWorkingHour, TimeOff, WorkingHour
from apps.branches.models import BranchHoliday, OpeningHour
from apps.core.exceptions import BusinessError
from apps.core.utils import combine_local

pytestmark = pytest.mark.django_db


def slots_for(branch, barber, service, day) -> list[str]:
    return build_availability(branch.id, barber.id, service.id, day).available_slots()


class TestJanelaDeTrabalho:
    def test_gera_slots_dentro_do_expediente(self, branch, barber, service, next_workday):
        slots = slots_for(branch, barber, service, next_workday)
        assert slots
        assert slots[0] == "09:00"
        # Expediente do barbeiro termina às 18:00 e o serviço dura 30min.
        assert slots[-1] == "17:30"

    def test_intervalo_de_almoco_nao_e_ofertado(self, branch, barber, service, next_workday):
        slots = slots_for(branch, barber, service, next_workday)
        assert "12:00" not in slots
        assert "12:30" not in slots
        assert "13:00" in slots

    def test_sem_jornada_no_dia_nao_ha_slots(self, branch, barber, service, next_workday):
        WorkingHour.objects.filter(barber=barber, weekday=next_workday.weekday()).delete()
        assert slots_for(branch, barber, service, next_workday) == []

    def test_filial_fechada_nao_tem_slots(self, branch, barber, service, next_workday):
        OpeningHour.objects.filter(branch=branch, weekday=next_workday.weekday()).update(
            is_closed=True
        )
        assert slots_for(branch, barber, service, next_workday) == []

    def test_feriado_da_filial_bloqueia_o_dia(self, branch, barber, service, next_workday):
        BranchHoliday.objects.create(
            branch=branch, date=next_workday, description="Feriado municipal"
        )
        assert slots_for(branch, barber, service, next_workday) == []

    def test_feriado_com_horario_reduzido(self, branch, barber, service, next_workday):
        BranchHoliday.objects.create(
            branch=branch,
            date=next_workday,
            description="Véspera de feriado",
            opens_at=time(9, 0),
            closes_at=time(11, 0),
        )
        slots = slots_for(branch, barber, service, next_workday)
        assert slots == ["09:00", "09:30", "10:00", "10:30"]

    def test_horario_especial_sobrepoe_a_jornada(self, branch, barber, service, next_workday):
        SpecialWorkingHour.objects.create(
            barber=barber,
            branch=branch,
            date=next_workday,
            starts_at=time(14, 0),
            ends_at=time(16, 0),
        )
        slots = slots_for(branch, barber, service, next_workday)
        assert slots == ["14:00", "14:30", "15:00", "15:30"]

    def test_slot_deve_caber_inteiro_no_expediente(self, branch, barber, service, next_workday):
        service.duration_minutes = 120
        service.save(update_fields=["duration_minutes"])
        slots = slots_for(branch, barber, service, next_workday)
        assert "17:00" not in slots  # terminaria 19:00, além das 18:00
        assert "16:00" in slots


class TestOcupacao:
    def test_horario_ocupado_nao_e_ofertado(
        self, branch, barber, service, client_profile, next_workday
    ):
        Appointment.objects.create(
            client=client_profile,
            barber=barber,
            branch=branch,
            service=service,
            date=next_workday,
            start_time=time(10, 0),
            end_time=time(10, 30),
            price=service.price,
            status=AppointmentStatus.CONFIRMED,
        )
        slots = slots_for(branch, barber, service, next_workday)
        assert "10:00" not in slots
        assert "10:30" in slots

    def test_agendamento_cancelado_libera_o_horario(
        self, branch, barber, service, client_profile, next_workday
    ):
        Appointment.objects.create(
            client=client_profile,
            barber=barber,
            branch=branch,
            service=service,
            date=next_workday,
            start_time=time(10, 0),
            end_time=time(10, 30),
            price=service.price,
            status=AppointmentStatus.CANCELLED,
        )
        assert "10:00" in slots_for(branch, barber, service, next_workday)

    def test_servico_longo_bloqueia_slots_sobrepostos(
        self, branch, barber, service, client_profile, next_workday
    ):
        Appointment.objects.create(
            client=client_profile,
            barber=barber,
            branch=branch,
            service=service,
            date=next_workday,
            start_time=time(10, 0),
            end_time=time(11, 30),
            price=service.price,
            status=AppointmentStatus.CONFIRMED,
        )
        slots = slots_for(branch, barber, service, next_workday)
        for blocked in ("10:00", "10:30", "11:00"):
            assert blocked not in slots
        assert "11:30" in slots

    def test_ausencia_do_barbeiro_bloqueia_o_periodo(self, branch, barber, service, next_workday):
        TimeOff.objects.create(
            barber=barber,
            starts_at=combine_local(next_workday, time(9, 0)),
            ends_at=combine_local(next_workday, time(11, 0)),
            reason="Consulta médica",
        )
        slots = slots_for(branch, barber, service, next_workday)
        assert "09:00" not in slots
        assert "10:30" not in slots
        assert "11:00" in slots

    def test_ferias_cobrindo_o_dia_inteiro(self, branch, barber, service, next_workday):
        TimeOff.objects.create(
            barber=barber,
            starts_at=combine_local(next_workday - timedelta(days=1), time(0, 0)),
            ends_at=combine_local(next_workday + timedelta(days=1), time(23, 59)),
            reason="Férias",
        )
        assert slots_for(branch, barber, service, next_workday) == []


class TestValidacoesDeContexto:
    def test_barbeiro_de_outra_filial_e_rejeitado(self, branch, barber, service, next_workday):
        from apps.branches.models import Branch

        other = Branch.objects.create(
            name="Outra",
            slug="outra",
            address="Rua B",
            number="1",
            district="Centro",
            city="Curitiba",
            state="PR",
            zip_code="80000000",
        )
        with pytest.raises(BusinessError) as exc:
            build_availability(other.id, barber.id, service.id, next_workday).validate_context()
        assert exc.value.business_code == "BARBER_NOT_IN_BRANCH"

    def test_servico_nao_executado_pelo_barbeiro_e_rejeitado(self, branch, barber, next_workday):
        from apps.services.models import Service

        other_service = Service.objects.create(
            name="Outro", slug="outro", duration_minutes=30, price="10.00"
        )
        with pytest.raises(BusinessError) as exc:
            build_availability(
                branch.id, barber.id, other_service.id, next_workday
            ).validate_context()
        assert exc.value.business_code == "BARBER_DOES_NOT_PERFORM_SERVICE"

    def test_data_passada_e_rejeitada(self, branch, barber, service):
        yesterday = timezone.localdate() - timedelta(days=1)
        with pytest.raises(BusinessError) as exc:
            build_availability(branch.id, barber.id, service.id, yesterday).validate_context()
        assert exc.value.business_code == "DATE_IN_PAST"

    def test_data_alem_da_antecedencia_maxima_e_rejeitada(self, branch, barber, service):
        far = timezone.localdate() + timedelta(days=branch.max_advance_booking_days + 1)
        with pytest.raises(BusinessError) as exc:
            build_availability(branch.id, barber.id, service.id, far).validate_context()
        assert exc.value.business_code == "DATE_TOO_FAR"

    def test_barbeiro_inativo_e_rejeitado(self, branch, barber, service, next_workday):
        barber.is_active = False
        barber.save(update_fields=["is_active"])
        with pytest.raises(BusinessError) as exc:
            build_availability(branch.id, barber.id, service.id, next_workday).validate_context()
        assert exc.value.business_code == "BARBER_INACTIVE"
