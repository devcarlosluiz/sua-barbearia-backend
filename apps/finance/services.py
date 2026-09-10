"""Regras financeiras: lançamentos automáticos e comissões."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import BusinessError
from apps.finance.models import (
    Commission,
    CommissionStatus,
    Transaction,
    TransactionCategory,
    TransactionType,
)


def record_income(
    *,
    branch,
    amount: Decimal,
    description: str,
    category: str = TransactionCategory.SERVICES,
    date=None,
    payment=None,
    appointment=None,
    sale=None,
    barber=None,
    created_by=None,
) -> Transaction:
    """Cria uma receita automática vinculada à sua origem."""
    return Transaction.objects.create(
        branch=branch,
        type=TransactionType.INCOME,
        category=category,
        description=description,
        amount=Decimal(amount),
        date=date or timezone.localdate(),
        payment=payment,
        appointment=appointment,
        sale=sale,
        barber=barber,
        created_by=created_by,
        is_automatic=True,
    )


def record_expense(
    *,
    branch,
    amount: Decimal,
    description: str,
    category: str = TransactionCategory.OTHER,
    date=None,
    barber=None,
    created_by=None,
    is_automatic: bool = False,
) -> Transaction:
    return Transaction.objects.create(
        branch=branch,
        type=TransactionType.EXPENSE,
        category=category,
        description=description,
        amount=Decimal(amount),
        date=date or timezone.localdate(),
        barber=barber,
        created_by=created_by,
        is_automatic=is_automatic,
    )


def create_appointment_commission(appointment, base_amount: Decimal) -> Commission | None:
    """Gera a comissão do barbeiro pelo atendimento concluído."""
    percentage = appointment.barber.commission_percentage or Decimal("0")
    if percentage <= 0 or base_amount <= 0:
        return None

    amount = (Decimal(base_amount) * percentage / Decimal("100")).quantize(Decimal("0.01"))
    commission, _ = Commission.objects.update_or_create(
        appointment=appointment,
        defaults={
            "barber": appointment.barber,
            "branch": appointment.branch,
            "base_amount": Decimal(base_amount),
            "percentage": percentage,
            "amount": amount,
            "reference_date": appointment.date,
            "status": CommissionStatus.PENDING,
        },
    )
    return commission


def create_sale_item_commission(sale_item, branch, barber) -> Commission | None:
    """Gera a comissão do barbeiro pela venda de um produto."""
    percentage = sale_item.commission_percentage or Decimal("0")
    if barber is None or percentage <= 0:
        return None

    amount = (sale_item.total * percentage / Decimal("100")).quantize(Decimal("0.01"))
    commission, _ = Commission.objects.update_or_create(
        sale_item=sale_item,
        defaults={
            "barber": barber,
            "branch": branch,
            "base_amount": sale_item.total,
            "percentage": percentage,
            "amount": amount,
            "reference_date": timezone.localdate(),
            "status": CommissionStatus.PENDING,
        },
    )
    return commission


@transaction.atomic
def pay_commissions(*, commission_ids: list[int], created_by=None) -> dict[str, Any]:
    """Marca comissões como pagas e lança a despesa correspondente por filial."""
    commissions = list(
        Commission.objects.select_for_update()
        .select_related("barber__user", "branch")
        .filter(pk__in=commission_ids, status=CommissionStatus.PENDING)
    )
    if not commissions:
        raise BusinessError(
            "Nenhuma comissão pendente encontrada para os itens informados.",
            code="NO_PENDING_COMMISSIONS",
        )

    now = timezone.now()
    grouped: dict[tuple[int, int], list[Commission]] = {}
    for commission in commissions:
        grouped.setdefault((commission.branch_id, commission.barber_id), []).append(commission)

    total = Decimal("0.00")
    for items in grouped.values():
        subtotal = sum((item.amount for item in items), Decimal("0.00"))
        expense = record_expense(
            branch=items[0].branch,
            amount=subtotal,
            description=f"Pagamento de comissões - {items[0].barber.display_name}",
            category=TransactionCategory.COMMISSION,
            barber=items[0].barber,
            created_by=created_by,
            is_automatic=True,
        )
        Commission.objects.filter(pk__in=[item.pk for item in items]).update(
            status=CommissionStatus.PAID, paid_at=now, payout_transaction=expense
        )
        total += subtotal

    return {"paid_count": len(commissions), "total_amount": total}


def cancel_appointment_commission(appointment) -> None:
    """Cancela a comissão de um atendimento revertido."""
    Commission.objects.filter(appointment=appointment, status=CommissionStatus.PENDING).update(
        status=CommissionStatus.CANCELLED
    )
