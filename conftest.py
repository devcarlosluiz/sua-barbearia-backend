"""Fixtures compartilhadas pela suíte de testes da Sua Barbearia."""

from __future__ import annotations

from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, UserRole
from apps.appointments.models import Appointment, AppointmentStatus
from apps.barbers.models import Barber, BarberService, WorkingHour
from apps.branches.models import Branch, OpeningHour
from apps.clients.models import Client
from apps.products.models import Product
from apps.services.models import Service

PASSWORD = "SenhaTeste@2026"


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def owner(db) -> User:
    return User.objects.create_user(
        email="owner@test.com",
        password=PASSWORD,
        first_name="Owner",
        last_name="Sua Barbearia",
        role=UserRole.OWNER,
        is_verified=True,
    )


@pytest.fixture
def branch(db) -> Branch:
    branch = Branch.objects.create(
        name="Sua Barbearia Teste",
        slug="sua-barbearia-teste",
        address="Rua Teste",
        number="100",
        district="Centro",
        city="Curitiba",
        state="PR",
        zip_code="80000000",
        slot_interval_minutes=30,
        cancellation_limit_hours=2,
    )
    for weekday in range(0, 6):
        OpeningHour.objects.create(
            branch=branch, weekday=weekday, opens_at=time(9, 0), closes_at=time(19, 0)
        )
    OpeningHour.objects.create(
        branch=branch, weekday=6, opens_at=time(9, 0), closes_at=time(13, 0), is_closed=True
    )
    return branch


@pytest.fixture
def service(db) -> Service:
    return Service.objects.create(
        name="Corte Teste",
        slug="corte-teste",
        duration_minutes=30,
        price=Decimal("50.00"),
    )


@pytest.fixture
def barber(db, branch, service) -> Barber:
    user = User.objects.create_user(
        email="barber@test.com",
        password=PASSWORD,
        first_name="Barbeiro",
        last_name="Teste",
        role=UserRole.BARBER,
        is_verified=True,
    )
    barber = Barber.objects.create(user=user, commission_percentage=Decimal("40.00"))
    barber.branches.add(branch)
    BarberService.objects.create(barber=barber, service=service)
    for weekday in range(0, 6):
        WorkingHour.objects.create(
            barber=barber,
            branch=branch,
            weekday=weekday,
            starts_at=time(9, 0),
            ends_at=time(18, 0),
            break_starts_at=time(12, 0),
            break_ends_at=time(13, 0),
        )
    return barber


@pytest.fixture
def client_profile(db, branch) -> Client:
    user = User.objects.create_user(
        email="client@test.com",
        password=PASSWORD,
        first_name="Cliente",
        last_name="Teste",
        role=UserRole.CLIENT,
        is_verified=True,
    )
    return Client.objects.create(user=user, preferred_branch=branch)


@pytest.fixture
def other_client(db, branch) -> Client:
    user = User.objects.create_user(
        email="client2@test.com",
        password=PASSWORD,
        first_name="Outro",
        last_name="Cliente",
        role=UserRole.CLIENT,
    )
    return Client.objects.create(user=user, preferred_branch=branch)


@pytest.fixture
def product(db) -> Product:
    return Product.objects.create(
        name="Pomada Teste",
        sku="TST-001",
        cost_price=Decimal("10.00"),
        sale_price=Decimal("30.00"),
        commission_percentage=Decimal("10.00"),
    )


@pytest.fixture
def next_workday() -> date:
    """Próximo dia útil (segunda a sábado) a partir de amanhã."""
    day = timezone.localdate() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


@pytest.fixture
def auth(api):
    """Autentica o APIClient com o token JWT do usuário informado."""

    def _auth(user: User) -> APIClient:
        response = api.post(
            "/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}, format="json"
        )
        assert response.status_code == 200, response.content
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['data']['access']}")
        return api

    return _auth


@pytest.fixture
def appointment(db, branch, barber, service, client_profile, next_workday) -> Appointment:
    return Appointment.objects.create(
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
