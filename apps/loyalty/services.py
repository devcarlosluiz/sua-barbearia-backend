"""Regras do programa de fidelidade."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from django.db import transaction

from apps.branches.models import Branch
from apps.clients.models import Client
from apps.core.exceptions import BusinessError
from apps.loyalty.models import (
    LoyaltyAccount,
    LoyaltyReward,
    LoyaltyTransaction,
    LoyaltyTransactionType,
)


def get_or_create_account(client: Client) -> LoyaltyAccount:
    account, _ = LoyaltyAccount.objects.get_or_create(client=client)
    return account


def calculate_points(amount: Decimal, branch: Branch) -> int:
    """Pontos ganhos por um valor pago nesta filial (arredonda para baixo)."""
    rate = branch.loyalty_points_per_currency_unit or Decimal("0")
    return int((Decimal(amount) * rate).to_integral_value(rounding=ROUND_DOWN))


@transaction.atomic
def earn_points(
    *,
    client: Client,
    branch: Branch,
    amount: Decimal,
    description: str = "",
    appointment=None,
    sale=None,
    created_by=None,
) -> LoyaltyTransaction | None:
    """Credita pontos referentes a um valor pago."""
    points = calculate_points(amount, branch)
    if points <= 0:
        return None

    account = LoyaltyAccount.objects.select_for_update().filter(client=client).first()
    if account is None:
        account = LoyaltyAccount.objects.create(client=client)
        account = LoyaltyAccount.objects.select_for_update().get(pk=account.pk)

    account.balance += points
    account.lifetime_earned += points
    account.save(update_fields=["balance", "lifetime_earned", "updated_at"])

    Client.objects.filter(pk=client.pk).update(loyalty_points=account.balance)

    return LoyaltyTransaction.objects.create(
        account=account,
        type=LoyaltyTransactionType.EARN,
        points=points,
        balance_after=account.balance,
        description=description or f"Pontos por R$ {Decimal(amount):.2f}",
        appointment=appointment,
        sale=sale,
        created_by=created_by,
    )


@transaction.atomic
def redeem_reward(*, client: Client, reward: LoyaltyReward, created_by=None) -> LoyaltyTransaction:
    """Resgata uma recompensa debitando os pontos da conta do cliente."""
    if not reward.is_active:
        raise BusinessError("Esta recompensa não está disponível.", code="REWARD_INACTIVE")

    account = LoyaltyAccount.objects.select_for_update().filter(client=client).first()
    if account is None:
        raise BusinessError(
            "Conta de fidelidade não encontrada.", code="LOYALTY_ACCOUNT_NOT_FOUND", status_code=404
        )
    if account.balance < reward.points_cost:
        raise BusinessError(
            f"Saldo insuficiente. Você tem {account.balance} pontos e precisa de "
            f"{reward.points_cost}.",
            code="INSUFFICIENT_POINTS",
        )

    account.balance -= reward.points_cost
    account.lifetime_redeemed += reward.points_cost
    account.save(update_fields=["balance", "lifetime_redeemed", "updated_at"])
    Client.objects.filter(pk=client.pk).update(loyalty_points=account.balance)

    return LoyaltyTransaction.objects.create(
        account=account,
        type=LoyaltyTransactionType.REDEEM,
        points=-reward.points_cost,
        balance_after=account.balance,
        description=f"Resgate: {reward.name}",
        reward=reward,
        created_by=created_by,
    )


@transaction.atomic
def adjust_points(
    *, client: Client, points: int, description: str, created_by=None
) -> LoyaltyTransaction:
    """Ajuste manual de saldo pelo OWNER (positivo ou negativo)."""
    if points == 0:
        raise BusinessError(
            "Informe uma quantidade de pontos diferente de zero.", code="INVALID_POINTS"
        )

    account = LoyaltyAccount.objects.select_for_update().filter(client=client).first()
    if account is None:
        account = LoyaltyAccount.objects.create(client=client)
        account = LoyaltyAccount.objects.select_for_update().get(pk=account.pk)

    if account.balance + points < 0:
        raise BusinessError("O ajuste deixaria o saldo negativo.", code="INSUFFICIENT_POINTS")

    account.balance += points
    if points > 0:
        account.lifetime_earned += points
    else:
        account.lifetime_redeemed += abs(points)
    account.save(update_fields=["balance", "lifetime_earned", "lifetime_redeemed", "updated_at"])
    Client.objects.filter(pk=client.pk).update(loyalty_points=account.balance)

    return LoyaltyTransaction.objects.create(
        account=account,
        type=LoyaltyTransactionType.ADJUSTMENT,
        points=points,
        balance_after=account.balance,
        description=description,
        created_by=created_by,
    )
