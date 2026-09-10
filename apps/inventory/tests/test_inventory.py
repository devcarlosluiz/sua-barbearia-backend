"""Testes de estoque e venda de produtos."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.exceptions import BusinessError
from apps.core.testing import data_of
from apps.finance.models import Commission, Transaction, TransactionCategory, TransactionType
from apps.inventory.models import (
    SaleStatus,
    StockItem,
    StockMovement,
    StockMovementType,
)
from apps.inventory.services import cancel_sale, complete_sale, create_sale, register_movement
from apps.payments.models import Payment, PaymentStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def stocked(db, product, branch, owner):
    register_movement(
        product_id=product.id,
        branch_id=branch.id,
        movement_type=StockMovementType.ENTRY,
        quantity=10,
        unit_cost=product.cost_price,
        reason="Carga inicial",
        created_by=owner,
    )
    return StockItem.objects.get(product=product, branch=branch)


class TestMovimentacoes:
    def test_entrada_aumenta_o_saldo(self, product, branch, owner):
        movement = register_movement(
            product_id=product.id,
            branch_id=branch.id,
            movement_type=StockMovementType.ENTRY,
            quantity=15,
            created_by=owner,
        )
        assert movement.previous_quantity == 0
        assert movement.new_quantity == 15
        assert StockItem.objects.get(product=product, branch=branch).quantity == 15

    def test_perda_reduz_o_saldo(self, stocked, product, branch, owner):
        register_movement(
            product_id=product.id,
            branch_id=branch.id,
            movement_type=StockMovementType.LOSS,
            quantity=3,
            reason="Produto danificado",
            created_by=owner,
        )
        stocked.refresh_from_db()
        assert stocked.quantity == 7

    def test_ajuste_define_o_saldo_final(self, stocked, product, branch, owner):
        movement = register_movement(
            product_id=product.id,
            branch_id=branch.id,
            movement_type=StockMovementType.ADJUSTMENT,
            quantity=4,
            reason="Contagem física",
            created_by=owner,
        )
        assert movement.previous_quantity == 10
        assert movement.new_quantity == 4
        assert movement.quantity == -6

    def test_saida_maior_que_o_saldo_e_bloqueada(self, stocked, product, branch, owner):
        with pytest.raises(BusinessError) as exc:
            register_movement(
                product_id=product.id,
                branch_id=branch.id,
                movement_type=StockMovementType.LOSS,
                quantity=50,
                created_by=owner,
            )
        assert exc.value.business_code == "INSUFFICIENT_STOCK"
        stocked.refresh_from_db()
        assert stocked.quantity == 10

    def test_toda_movimentacao_gera_historico(self, stocked, product, branch, owner):
        register_movement(
            product_id=product.id,
            branch_id=branch.id,
            movement_type=StockMovementType.RETURN,
            quantity=2,
            created_by=owner,
        )
        assert StockMovement.objects.filter(product=product, branch=branch).count() == 2

    def test_quantidade_invalida_e_rejeitada(self, product, branch, owner):
        with pytest.raises(BusinessError) as exc:
            register_movement(
                product_id=product.id,
                branch_id=branch.id,
                movement_type=StockMovementType.ENTRY,
                quantity=0,
                created_by=owner,
            )
        assert exc.value.business_code == "INVALID_QUANTITY"


class TestVenda:
    def test_venda_concluida_baixa_estoque_e_lanca_caixa(
        self, stocked, product, branch, client_profile, barber, owner
    ):
        sale = create_sale(
            branch_id=branch.id,
            items=[{"product_id": product.id, "quantity": 2}],
            client_id=client_profile.id,
            barber_id=barber.id,
            created_by=owner,
        )
        assert sale.status == SaleStatus.OPEN
        assert sale.total == product.sale_price * 2

        complete_sale(sale=sale, payment_method="PIX", created_by=owner)
        sale.refresh_from_db()
        stocked.refresh_from_db()

        assert sale.status == SaleStatus.COMPLETED
        assert stocked.quantity == 8
        assert Payment.objects.filter(sale=sale, status=PaymentStatus.PAID).exists()
        assert Transaction.objects.filter(
            sale=sale, type=TransactionType.INCOME, category=TransactionCategory.PRODUCTS
        ).exists()
        assert Commission.objects.filter(sale_item__sale=sale).exists()

        client_profile.refresh_from_db()
        assert client_profile.loyalty_points == int(sale.total)

    def test_venda_com_estoque_insuficiente_falha(self, stocked, product, branch, owner):
        sale = create_sale(
            branch_id=branch.id,
            items=[{"product_id": product.id, "quantity": 100}],
            created_by=owner,
        )
        with pytest.raises(BusinessError) as exc:
            complete_sale(sale=sale, payment_method="CASH", created_by=owner)
        assert exc.value.business_code == "INSUFFICIENT_STOCK"

        stocked.refresh_from_db()
        assert stocked.quantity == 10
        sale.refresh_from_db()
        assert sale.status == SaleStatus.OPEN

    def test_venda_sem_itens_e_rejeitada(self, branch, owner):
        with pytest.raises(BusinessError) as exc:
            create_sale(branch_id=branch.id, items=[], created_by=owner)
        assert exc.value.business_code == "SALE_WITHOUT_ITEMS"

    def test_desconto_maior_que_o_total_e_rejeitado(self, stocked, product, branch, owner):
        with pytest.raises(BusinessError) as exc:
            create_sale(
                branch_id=branch.id,
                items=[{"product_id": product.id, "quantity": 1}],
                discount_amount=Decimal("999.00"),
                created_by=owner,
            )
        assert exc.value.business_code == "DISCOUNT_GREATER_THAN_TOTAL"

    def test_cancelamento_devolve_ao_estoque(self, stocked, product, branch, barber, owner):
        sale = create_sale(
            branch_id=branch.id,
            items=[{"product_id": product.id, "quantity": 3}],
            barber_id=barber.id,
            created_by=owner,
        )
        complete_sale(sale=sale, payment_method="PIX", created_by=owner)
        stocked.refresh_from_db()
        assert stocked.quantity == 7

        cancel_sale(sale=sale, reason="Cliente desistiu", created_by=owner)
        stocked.refresh_from_db()
        sale.refresh_from_db()

        assert stocked.quantity == 10
        assert sale.status == SaleStatus.CANCELLED
        assert Payment.objects.get(sale=sale).status == PaymentStatus.REFUNDED
        assert Commission.objects.get(sale_item__sale=sale).status == "CANCELLED"

    def test_venda_concluida_nao_conclui_novamente(self, stocked, product, branch, owner):
        sale = create_sale(
            branch_id=branch.id,
            items=[{"product_id": product.id, "quantity": 1}],
            created_by=owner,
        )
        complete_sale(sale=sale, payment_method="CASH", created_by=owner)
        with pytest.raises(BusinessError) as exc:
            complete_sale(sale=sale, payment_method="CASH", created_by=owner)
        assert exc.value.business_code == "SALE_NOT_OPEN"


class TestApiVendas:
    def test_owner_registra_venda_paga_em_uma_chamada(
        self, auth, owner, stocked, product, branch, client_profile
    ):
        api = auth(owner)
        response = api.post(
            "/api/v1/sales/",
            {
                "branch_id": branch.id,
                "client_id": client_profile.id,
                "items": [{"product_id": product.id, "quantity": 2}],
                "payment_method": "PIX",
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        assert data_of(response)["status"] == SaleStatus.COMPLETED
        stocked.refresh_from_db()
        assert stocked.quantity == 8

    def test_endpoint_de_estoque_baixo(self, auth, owner, stocked, product, branch):
        stocked.minimum_stock = 20
        stocked.save(update_fields=["minimum_stock"])
        api = auth(owner)
        response = api.get("/api/v1/inventory/stock/low-stock/")
        assert response.status_code == 200
        assert len(data_of(response)) == 1

    def test_movimentacao_pela_api_registra_historico(self, auth, owner, product, branch):
        api = auth(owner)
        response = api.post(
            "/api/v1/inventory/movements/",
            {
                "product_id": product.id,
                "branch_id": branch.id,
                "type": "ENTRY",
                "quantity": 7,
                "reason": "Compra do fornecedor",
            },
            format="json",
        )
        assert response.status_code == 201
        assert data_of(response)["new_quantity"] == 7
