"""Testes de avaliações."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.appointments.models import AppointmentStatus
from apps.core.testing import code_of, data_of
from apps.reviews.models import Review

pytestmark = pytest.mark.django_db


@pytest.fixture
def completed_appointment(appointment):
    appointment.status = AppointmentStatus.COMPLETED
    appointment.save(update_fields=["status"])
    return appointment


class TestCriacao:
    def test_cliente_avalia_atendimento_concluido(
        self, auth, client_profile, completed_appointment
    ):
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/reviews/",
            {"appointment": completed_appointment.id, "rating": 5, "comment": "Excelente!"},
            format="json",
        )
        assert response.status_code == 201, response.content
        review = Review.objects.get(appointment=completed_appointment)
        assert review.client_id == client_profile.id
        assert review.barber_id == completed_appointment.barber_id
        assert review.branch_id == completed_appointment.branch_id

    def test_avaliacao_duplicada_e_bloqueada(self, auth, client_profile, completed_appointment):
        api = auth(client_profile.user)
        payload = {"appointment": completed_appointment.id, "rating": 5}
        assert api.post("/api/v1/reviews/", payload, format="json").status_code == 201

        response = api.post("/api/v1/reviews/", payload, format="json")
        assert response.status_code == 400
        assert code_of(response) == "REVIEW_ALREADY_EXISTS"
        assert Review.objects.filter(appointment=completed_appointment).count() == 1

    def test_atendimento_nao_concluido_nao_pode_ser_avaliado(
        self, auth, client_profile, appointment
    ):
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/reviews/",
            {"appointment": appointment.id, "rating": 5},
            format="json",
        )
        assert response.status_code == 400
        assert code_of(response) == "APPOINTMENT_NOT_COMPLETED"

    def test_cliente_nao_avalia_atendimento_de_outro(
        self, auth, other_client, completed_appointment
    ):
        api = auth(other_client.user)
        response = api.post(
            "/api/v1/reviews/",
            {"appointment": completed_appointment.id, "rating": 1},
            format="json",
        )
        assert response.status_code == 403
        assert code_of(response) == "PERMISSION_DENIED"

    @pytest.mark.parametrize("rating", [0, 6, -1])
    def test_nota_fora_do_intervalo_e_rejeitada(
        self, auth, client_profile, completed_appointment, rating
    ):
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/reviews/",
            {"appointment": completed_appointment.id, "rating": rating},
            format="json",
        )
        assert response.status_code == 400


class TestAgregacaoDeNota:
    def test_avaliacao_atualiza_a_media_do_barbeiro(
        self, client_profile, completed_appointment, barber
    ):
        Review.objects.create(
            appointment=completed_appointment,
            client=client_profile,
            barber=barber,
            branch=completed_appointment.branch,
            rating=4,
        )
        barber.refresh_from_db()
        assert barber.rating == Decimal("4.00")
        assert barber.reviews_count == 1

    def test_exclusao_recalcula_a_media(self, client_profile, completed_appointment, barber):
        review = Review.objects.create(
            appointment=completed_appointment,
            client=client_profile,
            barber=barber,
            branch=completed_appointment.branch,
            rating=5,
        )
        review.delete()
        barber.refresh_from_db()
        assert barber.rating == Decimal("0.00")
        assert barber.reviews_count == 0


class TestConsultas:
    def test_resumo_traz_media_e_distribuicao(
        self, auth, owner, client_profile, completed_appointment, barber
    ):
        Review.objects.create(
            appointment=completed_appointment,
            client=client_profile,
            barber=barber,
            branch=completed_appointment.branch,
            rating=5,
        )
        api = auth(owner)
        response = api.get("/api/v1/reviews/summary/")
        assert response.status_code == 200
        payload = data_of(response)
        assert payload["total"] == 1
        assert payload["average"] == 5.0

    def test_pendentes_lista_atendimentos_sem_avaliacao(
        self, auth, client_profile, completed_appointment
    ):
        api = auth(client_profile.user)
        response = api.get("/api/v1/reviews/pending/")
        assert response.status_code == 200
        assert [item["id"] for item in data_of(response)] == [completed_appointment.id]

    def test_owner_responde_avaliacao(
        self, auth, owner, client_profile, completed_appointment, barber
    ):
        review = Review.objects.create(
            appointment=completed_appointment,
            client=client_profile,
            barber=barber,
            branch=completed_appointment.branch,
            rating=3,
        )
        api = auth(owner)
        response = api.post(
            f"/api/v1/reviews/{review.id}/reply/",
            {"reply": "Obrigado pelo retorno!"},
            format="json",
        )
        assert response.status_code == 200
        review.refresh_from_db()
        assert review.reply == "Obrigado pelo retorno!"
        assert review.replied_at is not None
