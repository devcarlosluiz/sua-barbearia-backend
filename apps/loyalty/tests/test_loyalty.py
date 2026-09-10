"""Testes do programa de fidelidade."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.exceptions import BusinessError
from apps.core.testing import code_of, data_of
from apps.loyalty.models import (
    LoyaltyAccount,
    LoyaltyReward,
    LoyaltyRewardType,
    LoyaltyTransaction,
    LoyaltyTransactionType,
)
from apps.loyalty.services import adjust_points, calculate_points, earn_points, redeem_reward

pytestmark = pytest.mark.django_db


@pytest.fixture
def reward(db) -> LoyaltyReward:
    return LoyaltyReward.objects.create(
        name="Desconto de R$ 20",
        type=LoyaltyRewardType.DISCOUNT_FIXED,
        points_cost=500,
        discount_value=Decimal("20.00"),
    )


class TestConta:
    def test_conta_e_criada_junto_com_o_cliente(self, client_profile):
        assert LoyaltyAccount.objects.filter(client=client_profile).exists()

    def test_saldo_inicial_e_zero(self, client_profile):
        assert client_profile.loyalty_account.balance == 0


class TestAcumulo:
    def test_calculo_usa_a_taxa_da_filial(self, branch):
        branch.loyalty_points_per_currency_unit = Decimal("2.00")
        branch.save(update_fields=["loyalty_points_per_currency_unit"])
        assert calculate_points(Decimal("50.00"), branch) == 100

    def test_calculo_arredonda_para_baixo(self, branch):
        assert calculate_points(Decimal("49.90"), branch) == 49

    def test_acumulo_credita_e_registra_movimento(self, client_profile, branch):
        movement = earn_points(client=client_profile, branch=branch, amount=Decimal("80.00"))
        assert movement.type == LoyaltyTransactionType.EARN
        assert movement.points == 80
        assert movement.balance_after == 80

        client_profile.refresh_from_db()
        assert client_profile.loyalty_points == 80
        assert client_profile.loyalty_account.balance == 80
        assert client_profile.loyalty_account.lifetime_earned == 80

    def test_acumulos_sucessivos_somam(self, client_profile, branch):
        earn_points(client=client_profile, branch=branch, amount=Decimal("30.00"))
        earn_points(client=client_profile, branch=branch, amount=Decimal("45.00"))
        client_profile.refresh_from_db()
        assert client_profile.loyalty_points == 75

    def test_valor_zero_nao_gera_movimento(self, client_profile, branch):
        assert earn_points(client=client_profile, branch=branch, amount=Decimal("0.00")) is None
        assert LoyaltyTransaction.objects.count() == 0


class TestResgate:
    def test_resgate_debita_os_pontos(self, client_profile, branch, reward):
        earn_points(client=client_profile, branch=branch, amount=Decimal("600.00"))
        movement = redeem_reward(client=client_profile, reward=reward)

        assert movement.type == LoyaltyTransactionType.REDEEM
        assert movement.points == -500
        assert movement.balance_after == 100

        client_profile.refresh_from_db()
        assert client_profile.loyalty_points == 100
        assert client_profile.loyalty_account.lifetime_redeemed == 500

    def test_resgate_sem_saldo_e_bloqueado(self, client_profile, branch, reward):
        earn_points(client=client_profile, branch=branch, amount=Decimal("100.00"))
        with pytest.raises(BusinessError) as exc:
            redeem_reward(client=client_profile, reward=reward)
        assert exc.value.business_code == "INSUFFICIENT_POINTS"

        client_profile.refresh_from_db()
        assert client_profile.loyalty_points == 100

    def test_recompensa_inativa_nao_e_resgatada(self, client_profile, branch, reward):
        earn_points(client=client_profile, branch=branch, amount=Decimal("600.00"))
        reward.is_active = False
        reward.save(update_fields=["is_active"])
        with pytest.raises(BusinessError) as exc:
            redeem_reward(client=client_profile, reward=reward)
        assert exc.value.business_code == "REWARD_INACTIVE"


class TestAjuste:
    def test_ajuste_positivo(self, client_profile):
        movement = adjust_points(
            client=client_profile, points=250, description="Cortesia de aniversário"
        )
        assert movement.type == LoyaltyTransactionType.ADJUSTMENT
        client_profile.refresh_from_db()
        assert client_profile.loyalty_points == 250

    def test_ajuste_negativo(self, client_profile, branch):
        earn_points(client=client_profile, branch=branch, amount=Decimal("300.00"))
        adjust_points(client=client_profile, points=-100, description="Correção")
        client_profile.refresh_from_db()
        assert client_profile.loyalty_points == 200

    def test_ajuste_que_deixaria_saldo_negativo_e_bloqueado(self, client_profile):
        with pytest.raises(BusinessError) as exc:
            adjust_points(client=client_profile, points=-50, description="Erro")
        assert exc.value.business_code == "INSUFFICIENT_POINTS"


class TestApiFidelidade:
    def test_cliente_consulta_o_proprio_extrato(self, auth, client_profile, branch):
        earn_points(client=client_profile, branch=branch, amount=Decimal("120.00"))
        api = auth(client_profile.user)
        response = api.get("/api/v1/loyalty/accounts/me/")
        assert response.status_code == 200
        payload = data_of(response)
        assert payload["account"]["balance"] == 120
        assert len(payload["transactions"]) == 1

    def test_cliente_resgata_recompensa(self, auth, client_profile, branch, reward):
        earn_points(client=client_profile, branch=branch, amount=Decimal("600.00"))
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/loyalty/rewards/redeem/", {"reward_id": reward.id}, format="json"
        )
        assert response.status_code == 200
        assert data_of(response)["points"] == -500

    def test_resgate_sem_saldo_retorna_codigo_de_negocio(self, auth, client_profile, reward):
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/loyalty/rewards/redeem/", {"reward_id": reward.id}, format="json"
        )
        assert response.status_code == 400
        assert code_of(response) == "INSUFFICIENT_POINTS"

    def test_cliente_nao_ajusta_pontos(self, auth, client_profile):
        api = auth(client_profile.user)
        response = api.post(
            "/api/v1/loyalty/rewards/adjust/",
            {"client_id": client_profile.id, "points": 9999, "description": "hack"},
            format="json",
        )
        assert response.status_code == 403

    def test_cliente_nao_ve_extrato_de_outro(self, auth, client_profile, other_client, branch):
        earn_points(client=other_client, branch=branch, amount=Decimal("500.00"))
        api = auth(client_profile.user)
        response = api.get("/api/v1/loyalty/transactions/")
        assert response.status_code == 200
        assert data_of(response)["results"] == []
