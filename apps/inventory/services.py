"""Regras de estoque e vendas de produtos.

Invariantes garantidas aqui:
  * Nenhuma alteração de saldo acontece sem um `StockMovement`.
  * O saldo nunca fica negativo (validado aqui e por constraint no banco).
  * Concluir uma venda é atômico: itens → estoque → pagamento → caixa → comissão.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import BusinessError
from apps.inventory.models import (
    MOVEMENT_SIGN,
    Sale,
    SaleItem,
    SaleStatus,
    StockItem,
    StockMovement,
    StockMovementType,
)

logger = logging.getLogger("suabarbearia.application")


def get_or_create_stock_item(product_id: int, branch_id: int) -> StockItem:
    item, _ = StockItem.objects.get_or_create(product_id=product_id, branch_id=branch_id)
    return item


@transaction.atomic
def register_movement(
    *,
    product_id: int,
    branch_id: int,
    movement_type: str,
    quantity: int,
    unit_cost: Decimal | None = None,
    reason: str = "",
    sale_item: SaleItem | None = None,
    created_by=None,
) -> StockMovement:
    """Registra uma movimentação e atualiza o saldo do produto na filial.

    Para `ADJUSTMENT`, `quantity` é o saldo final desejado (contagem física).
    Para os demais tipos, `quantity` é a quantidade movimentada (sempre > 0).
    """
    if movement_type != StockMovementType.ADJUSTMENT and quantity <= 0:
        raise BusinessError("A quantidade deve ser maior que zero.", code="INVALID_QUANTITY")
    if movement_type == StockMovementType.ADJUSTMENT and quantity < 0:
        raise BusinessError("O saldo ajustado não pode ser negativo.", code="INVALID_QUANTITY")

    get_or_create_stock_item(product_id, branch_id)
    stock = StockItem.objects.select_for_update().get(product_id=product_id, branch_id=branch_id)

    previous = stock.quantity
    if movement_type == StockMovementType.ADJUSTMENT:
        new_quantity = quantity
        moved = quantity - previous
    else:
        moved = MOVEMENT_SIGN[movement_type] * quantity
        new_quantity = previous + moved

    if new_quantity < 0:
        raise BusinessError(
            f"Estoque insuficiente. Saldo atual: {previous}, solicitado: {quantity}.",
            code="INSUFFICIENT_STOCK",
        )

    stock.quantity = new_quantity
    stock.save(update_fields=["quantity", "updated_at"])

    return StockMovement.objects.create(
        product_id=product_id,
        branch_id=branch_id,
        type=movement_type,
        quantity=moved,
        previous_quantity=previous,
        new_quantity=new_quantity,
        unit_cost=unit_cost,
        reason=reason,
        sale_item=sale_item,
        created_by=created_by,
    )


@transaction.atomic
def create_sale(
    *,
    branch_id: int,
    items: list[dict[str, Any]],
    client_id: int | None = None,
    barber_id: int | None = None,
    appointment_id: int | None = None,
    discount_amount: Decimal = Decimal("0.00"),
    notes: str = "",
    created_by=None,
) -> Sale:
    """Cria a venda com seus itens, sem baixar estoque (status OPEN)."""
    from apps.products.models import Product

    if not items:
        raise BusinessError("Informe ao menos um produto na venda.", code="SALE_WITHOUT_ITEMS")

    sale = Sale.objects.create(
        branch_id=branch_id,
        client_id=client_id,
        barber_id=barber_id,
        appointment_id=appointment_id,
        discount_amount=Decimal(discount_amount or 0),
        notes=notes,
        created_by=created_by,
        status=SaleStatus.OPEN,
    )

    product_ids = [item["product_id"] for item in items]
    products = {p.pk: p for p in Product.objects.filter(pk__in=product_ids, is_active=True)}

    for item in items:
        product = products.get(item["product_id"])
        if product is None:
            raise BusinessError(
                f"Produto {item['product_id']} não encontrado ou inativo.",
                code="PRODUCT_NOT_FOUND",
                status_code=404,
            )
        quantity = int(item["quantity"])
        if quantity <= 0:
            raise BusinessError("A quantidade deve ser maior que zero.", code="INVALID_QUANTITY")

        unit_price = Decimal(item.get("unit_price") or product.sale_price)
        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=quantity,
            unit_price=unit_price,
            total=(unit_price * quantity).quantize(Decimal("0.01")),
            commission_percentage=product.commission_percentage,
        )

    sale.recalculate()
    if sale.total < 0:
        raise BusinessError(
            "O desconto não pode ser maior que o total da venda.",
            code="DISCOUNT_GREATER_THAN_TOTAL",
        )
    sale.save(update_fields=["subtotal", "total", "updated_at"])
    return sale


@transaction.atomic
def complete_sale(*, sale: Sale, payment_method: str, created_by=None) -> Sale:
    """Conclui a venda: baixa estoque, registra pagamento, caixa, comissão e pontos."""
    from apps.finance.models import TransactionCategory
    from apps.finance.services import create_sale_item_commission, record_income
    from apps.loyalty.services import earn_points
    from apps.payments.models import Payment, PaymentStatus

    if sale.status != SaleStatus.OPEN:
        raise BusinessError("Esta venda já foi finalizada ou cancelada.", code="SALE_NOT_OPEN")

    items = list(sale.items.select_related("product"))
    if not items:
        raise BusinessError("A venda não possui itens.", code="SALE_WITHOUT_ITEMS")

    for item in items:
        register_movement(
            product_id=item.product_id,
            branch_id=sale.branch_id,
            movement_type=StockMovementType.SALE,
            quantity=item.quantity,
            unit_cost=item.product.cost_price,
            reason=f"Venda {sale.uuid}",
            sale_item=item,
            created_by=created_by,
        )
        if sale.barber_id:
            create_sale_item_commission(item, sale.branch, sale.barber)

    now = timezone.now()
    sale.status = SaleStatus.COMPLETED
    sale.completed_at = now
    sale.save(update_fields=["status", "completed_at", "updated_at"])

    payment = Payment.objects.create(
        branch=sale.branch,
        sale=sale,
        client=sale.client,
        amount=sale.subtotal,
        discount_amount=sale.discount_amount,
        method=payment_method,
        status=PaymentStatus.PAID,
        paid_at=now,
        notes=f"Venda de produtos {sale.uuid}",
        created_by=created_by,
    )

    record_income(
        branch=sale.branch,
        amount=sale.total,
        description=f"Venda de produtos ({len(items)} item(ns))",
        category=TransactionCategory.PRODUCTS,
        payment=payment,
        sale=sale,
        barber=sale.barber,
        created_by=created_by,
    )

    if sale.client_id:
        earn_points(
            client=sale.client,
            branch=sale.branch,
            amount=sale.total,
            description="Compra de produtos",
            sale=sale,
            created_by=created_by,
        )

    return sale


@transaction.atomic
def cancel_sale(*, sale: Sale, reason: str = "", created_by=None) -> Sale:
    """Cancela a venda, devolvendo ao estoque os itens já baixados."""
    if sale.status == SaleStatus.CANCELLED:
        raise BusinessError("Esta venda já está cancelada.", code="SALE_ALREADY_CANCELLED")

    if sale.status == SaleStatus.COMPLETED:
        for item in sale.items.select_related("product"):
            register_movement(
                product_id=item.product_id,
                branch_id=sale.branch_id,
                movement_type=StockMovementType.RETURN,
                quantity=item.quantity,
                reason=f"Cancelamento da venda {sale.uuid}. {reason}".strip(),
                sale_item=item,
                created_by=created_by,
            )

        from apps.finance.models import Commission, CommissionStatus
        from apps.payments.models import PaymentStatus

        Commission.objects.filter(sale_item__sale=sale, status=CommissionStatus.PENDING).update(
            status=CommissionStatus.CANCELLED
        )
        sale.payments.filter(status=PaymentStatus.PAID).update(
            status=PaymentStatus.REFUNDED, refunded_at=timezone.now()
        )

    sale.status = SaleStatus.CANCELLED
    sale.notes = f"{sale.notes} | Cancelada: {reason}".strip(" |")
    sale.save(update_fields=["status", "notes", "updated_at"])
    return sale
