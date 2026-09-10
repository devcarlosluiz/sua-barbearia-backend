"""Testes do financeiro: lançamentos, caixa e comissões."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.exceptions import BusinessError
from apps.core.testing import data_of
from apps.finance.models import (
    CommissionStatus,
    TransactionCategory,
    TransactionType,
)
from apps.finance.services import (
    create_appointment_commission,
    pay_commissions,
    record_expense,
    record_income,
)

pytestmark = pytest.mark.django_db


class TestLancamentos:
    def test_receita_e_registrada(self, branch, owner):
        transaction = record_income(
            branch=branch,
            amount=Decimal("150.00"),
            description="Serviço avulso",
            created_by=owner,
        )
        assert transaction.type == TransactionType.INCOME
        assert transaction.signed_amount == Decimal("150.00")
        assert transaction.is_automatic is True

    def test_despesa_e_registrada(self, branch, owner):
        transaction = record_expense(
            branch=branch,
            amount=Decimal("500.00"),
            description="Aluguel",
            category=TransactionCategory.RENT,
            created_by=owner,
        )
        assert transaction.type == TransactionType.EXPENSE
        assert transaction.signed_amount == Decimal("-500.00")

    def test_owner_cria_lancamento_manual_pela_api(self, auth, owner, branch):
        api = auth(owner)
        response = api.post(
            "/api/v1/finance/transactions/",
            {
                "branch": branch.id,
                "type": "EXPENSE",
                "category": "MARKETING",
                "description": "Anúncios",
                "amount": "300.00",
                "date": timezone.localdate().isoformat(),
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        assert data_of(response)["is_automatic"] is False

    def test_valor_negativo_e_rejeitado(self, auth, owner, branch):
        api = auth(owner)
        response = api.post(
            "/api/v1/finance/transactions/",
            {
                "branch": branch.id,
                "type": "INCOME",
                "description": "Inválido",
                "amount": "-10.00",
                "date": timezone.localdate().isoformat(),
            },
            format="json",
        )
        assert response.status_code == 400


class TestResumoDeCaixa:
    def test_resumo_calcula_saldo_do_periodo(self, auth, owner, branch):
        record_income(
            branch=branch, amount=Decimal("1000.00"), description="Serviços", created_by=owner
        )
        record_expense(
            branch=branch, amount=Decimal("400.00"), description="Insumos", created_by=owner
        )

        api = auth(owner)
        response = api.get("/api/v1/finance/transactions/summary/", {"period": "30d"})
        assert response.status_code == 200

        payload = data_of(response)
        assert Decimal(str(payload["total_income"])) == Decimal("1000.00")
        assert Decimal(str(payload["total_expense"])) == Decimal("400.00")
        assert Decimal(str(payload["balance"])) == Decimal("600.00")

    def test_exportacao_csv(self, auth, owner, branch):
        record_income(
            branch=branch, amount=Decimal("100.00"), description="Corte", created_by=owner
        )
        api = auth(owner)
        response = api.get("/api/v1/finance/transactions/export/", {"period": "30d"})
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "Corte" in response.content.decode("utf-8")


class TestComissoes:
    def test_comissao_usa_o_percentual_do_barbeiro(self, appointment):
        commission = create_appointment_commission(appointment, appointment.price)
        assert commission.percentage == Decimal("40.00")
        assert commission.amount == Decimal("20.00")  # 40% de 50
        assert commission.status == CommissionStatus.PENDING

    def test_percentual_zero_nao_gera_comissao(self, appointment, barber):
        barber.commission_percentage = Decimal("0.00")
        barber.save(update_fields=["commission_percentage"])
        assert create_appointment_commission(appointment, appointment.price) is None

    def test_pagamento_de_comissoes_gera_despesa(self, appointment, owner):
        commission = create_appointment_commission(appointment, appointment.price)
        result = pay_commissions(commission_ids=[commission.id], created_by=owner)

        assert result["paid_count"] == 1
        assert result["total_amount"] == Decimal("20.00")

        commission.refresh_from_db()
        assert commission.status == CommissionStatus.PAID
        assert commission.paid_at is not None
        assert commission.payout_transaction is not None
        assert commission.payout_transaction.category == TransactionCategory.COMMISSION
        assert commission.payout_transaction.type == TransactionType.EXPENSE

    def test_pagar_comissao_ja_paga_falha(self, appointment, owner):
        commission = create_appointment_commission(appointment, appointment.price)
        pay_commissions(commission_ids=[commission.id], created_by=owner)
        with pytest.raises(BusinessError) as exc:
            pay_commissions(commission_ids=[commission.id], created_by=owner)
        assert exc.value.business_code == "NO_PENDING_COMMISSIONS"

    def test_barbeiro_ve_somente_as_proprias_comissoes(
        self, auth, appointment, barber, branch, other_client, service
    ):
        from datetime import time

        from apps.accounts.models import User, UserRole
        from apps.appointments.models import Appointment
        from apps.barbers.models import Barber

        create_appointment_commission(appointment, appointment.price)

        other_user = User.objects.create_user(
            email="b2@test.com",
            password="SenhaTeste@2026",
            first_name="B2",
            role=UserRole.BARBER,
        )
        other_barber = Barber.objects.create(user=other_user)
        other_barber.branches.add(branch)
        other_appointment = Appointment.objects.create(
            client=other_client,
            barber=other_barber,
            branch=branch,
            service=service,
            date=appointment.date,
            start_time=time(16, 0),
            end_time=time(16, 30),
            price=service.price,
        )
        create_appointment_commission(other_appointment, other_appointment.price)

        api = auth(barber.user)
        results = data_of(api.get("/api/v1/finance/commissions/"))["results"]
        assert {item["barber"] for item in results} == {barber.id}

    def test_barbeiro_nao_paga_comissoes(self, auth, barber, appointment):
        commission = create_appointment_commission(appointment, appointment.price)
        api = auth(barber.user)
        response = api.post(
            "/api/v1/finance/commissions/pay/",
            {"commission_ids": [commission.id]},
            format="json",
        )
        assert response.status_code == 403
        commission.refresh_from_db()
        assert commission.status == CommissionStatus.PENDING
