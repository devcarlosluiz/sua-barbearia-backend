"""Testes de permissão por papel.

Regra do projeto: o backend nunca confia no frontend. Cada papel só acessa o
que lhe pertence, mesmo que a rota seja chamada diretamente.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


class TestClientPermissions:
    """CLIENT não pode acessar áreas administrativas."""

    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/finance/transactions/",
            "/api/v1/finance/commissions/",
            "/api/v1/dashboard/owner/",
            "/api/v1/dashboard/barber/",
            "/api/v1/users/",
            "/api/v1/special-hours/",
            "/api/v1/inventory/movements/",
            "/api/v1/sales/",
            "/api/v1/products/",
        ],
    )
    def test_cliente_recebe_403(self, auth, client_profile, url):
        client = auth(client_profile.user)
        assert client.get(url).status_code == 403

    def test_cliente_nao_cria_filial(self, auth, client_profile):
        client = auth(client_profile.user)
        response = client.post("/api/v1/branches/", {"name": "Pirata"}, format="json")
        assert response.status_code == 403

    def test_cliente_nao_cria_servico(self, auth, client_profile):
        client = auth(client_profile.user)
        response = client.post(
            "/api/v1/services/",
            {"name": "Grátis", "duration_minutes": 30, "price": "0.00"},
            format="json",
        )
        assert response.status_code == 403

    def test_cliente_le_filiais(self, auth, client_profile, branch):
        client = auth(client_profile.user)
        assert client.get("/api/v1/branches/").status_code == 200


class TestBarberPermissions:
    """BARBER acessa a operação, mas não o financeiro global nem cadastros."""

    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/finance/transactions/",
            "/api/v1/dashboard/owner/",
            "/api/v1/users/",
        ],
    )
    def test_barbeiro_recebe_403(self, auth, barber, url):
        client = auth(barber.user)
        assert client.get(url).status_code == 403

    def test_barbeiro_ve_as_proprias_comissoes(self, auth, barber):
        client = auth(barber.user)
        assert client.get("/api/v1/finance/commissions/").status_code == 200

    def test_barbeiro_ve_o_proprio_dashboard(self, auth, barber):
        client = auth(barber.user)
        assert client.get("/api/v1/dashboard/barber/").status_code == 200

    def test_barbeiro_nao_cria_barbeiro(self, auth, barber):
        client = auth(barber.user)
        response = client.post("/api/v1/barbers/", {"nickname": "Fake"}, format="json")
        assert response.status_code == 403


class TestOwnerPermissions:
    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/finance/transactions/",
            "/api/v1/finance/commissions/",
            "/api/v1/dashboard/owner/",
            "/api/v1/users/",
            "/api/v1/products/",
            "/api/v1/sales/",
            "/api/v1/branches/",
            "/api/v1/clients/",
        ],
    )
    def test_owner_acessa_tudo(self, auth, owner, url):
        client = auth(owner)
        assert client.get(url).status_code == 200

    def test_owner_nao_acessa_dashboard_do_barbeiro(self, auth, owner):
        """Cada painel é de um papel; o OWNER usa o seu próprio."""
        client = auth(owner)
        assert client.get("/api/v1/dashboard/barber/").status_code == 403


class TestAnonymous:
    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/branches/",
            "/api/v1/services/",
            "/api/v1/appointments/",
            "/api/v1/auth/me/",
        ],
    )
    def test_sem_token_recebe_401(self, api, url):
        assert api.get(url).status_code == 401

    def test_health_check_e_publico(self, api):
        assert api.get("/health/").status_code == 200


class TestQuerysetScoping:
    """Um barbeiro/cliente nunca enxerga dados de outro."""

    def test_barbeiro_so_ve_os_proprios_agendamentos(
        self, auth, barber, appointment, branch, service, other_client
    ):
        from datetime import time

        from apps.accounts.models import User, UserRole
        from apps.appointments.models import Appointment
        from apps.barbers.models import Barber

        other_user = User.objects.create_user(
            email="barber2@test.com",
            password="SenhaTeste@2026",
            first_name="Outro",
            role=UserRole.BARBER,
        )
        other_barber = Barber.objects.create(user=other_user)
        other_barber.branches.add(branch)
        Appointment.objects.create(
            client=other_client,
            barber=other_barber,
            branch=branch,
            service=service,
            date=appointment.date,
            start_time=time(15, 0),
            end_time=time(15, 30),
            price=service.price,
        )

        client = auth(barber.user)
        results = client.get("/api/v1/appointments/").json()["data"]["results"]
        assert {item["barber"] for item in results} == {barber.id}

    def test_cliente_so_ve_os_proprios_agendamentos(
        self, auth, client_profile, other_client, appointment
    ):
        client = auth(other_client.user)
        results = client.get("/api/v1/appointments/").json()["data"]["results"]
        assert results == []
