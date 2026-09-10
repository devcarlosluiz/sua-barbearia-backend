"""API de planos: CRUD do proprietário e vitrine do cliente."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.testing import data_of, results_of
from apps.plans.models import Plan, PlanService

pytestmark = pytest.mark.django_db


class TestCrudDoProprietario:
    def test_owner_cria_plano_com_servicos(self, auth, owner, branch, service):
        api = auth(owner)
        response = api.post(
            "/api/v1/plans/",
            {
                "name": "Corte Mensal",
                "description": "Um corte por semana",
                "price": "129.90",
                "overage_discount_percentage": "20.00",
                "branches": [branch.id],
                "service_items": [{"service": service.id, "monthly_quota": 4}],
            },
            format="json",
        )
        assert response.status_code == 201, response.content

        plan = Plan.objects.get(name="Corte Mensal")
        assert plan.price == Decimal("129.90")
        assert plan.plan_services.get(service=service).monthly_quota == 4
        assert list(plan.branches.all()) == [branch]

    def test_plano_sem_servico_e_rejeitado(self, auth, owner):
        """Um plano vazio não entrega nada ao assinante."""
        api = auth(owner)
        response = api.post(
            "/api/v1/plans/",
            {"name": "Vazio", "price": "50.00", "service_items": []},
            format="json",
        )
        assert response.status_code == 400
        assert "service_items" in str(response.content, "utf-8")

    def test_servico_repetido_e_rejeitado(self, auth, owner, service):
        api = auth(owner)
        response = api.post(
            "/api/v1/plans/",
            {
                "name": "Duplicado",
                "price": "50.00",
                "service_items": [
                    {"service": service.id, "monthly_quota": 1},
                    {"service": service.id, "monthly_quota": 2},
                ],
            },
            format="json",
        )
        assert response.status_code == 400

    def test_atualizar_composicao_remove_o_que_saiu(self, auth, owner, plan, service, branch):
        from apps.services.models import Service

        outro = Service.objects.create(name="Barba", duration_minutes=20, price=Decimal("30.00"))
        api = auth(owner)

        response = api.patch(
            f"/api/v1/plans/{plan.id}/",
            {"service_items": [{"service": outro.id, "monthly_quota": 1}]},
            format="json",
        )
        assert response.status_code == 200, response.content

        remaining = list(plan.plan_services.values_list("service_id", flat=True))
        assert remaining == [outro.id]

    def test_patch_parcial_nao_apaga_a_composicao(self, auth, owner, plan, service):
        """Editar só o preço não pode zerar os serviços do plano."""
        api = auth(owner)
        response = api.patch(f"/api/v1/plans/{plan.id}/", {"price": "109.90"}, format="json")
        assert response.status_code == 200, response.content

        plan.refresh_from_db()
        assert plan.price == Decimal("109.90")
        assert plan.plan_services.count() == 1
        assert plan.plan_services.first().monthly_quota == 2

    def test_listagem_do_owner_traz_contagem_de_assinantes(
        self, auth, owner, plan, client_profile, fake_gateway
    ):
        from apps.plans.services import confirm_invoice, subscribe

        subscription = subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")
        confirm_invoice(subscription.invoices.first())

        api = auth(owner)
        rows = results_of(api.get("/api/v1/plans/"))
        assert rows[0]["subscribers_count"] == 1


class TestVitrineDoCliente:
    def test_cliente_ve_planos_publicos(self, auth, client_profile, plan):
        api = auth(client_profile.user)
        rows = results_of(api.get("/api/v1/plans/"))
        assert [row["name"] for row in rows] == [plan.name]
        assert rows[0]["plan_services"][0]["monthly_quota"] == 2

    def test_cliente_nao_ve_plano_oculto_nem_inativo(self, auth, client_profile, plan):
        plan.is_public = False
        plan.save(update_fields=["is_public"])

        api = auth(client_profile.user)
        assert results_of(api.get("/api/v1/plans/")) == []
        # E nem por acesso direto ao detalhe.
        assert api.get(f"/api/v1/plans/{plan.id}/").status_code == 404

    def test_cliente_nao_cria_plano(self, auth, client_profile):
        api = auth(client_profile.user)
        response = api.post("/api/v1/plans/", {"name": "Meu plano", "price": "1.00"}, format="json")
        assert response.status_code == 403

    def test_barbeiro_le_mas_nao_escreve(self, auth, barber, plan):
        api = auth(barber.user)
        assert results_of(api.get("/api/v1/plans/"))
        assert api.delete(f"/api/v1/plans/{plan.id}/").status_code == 403


class TestExclusaoDePlano:
    def test_plano_com_assinante_nao_e_excluido(
        self, auth, owner, plan, client_profile, fake_gateway
    ):
        """Excluir arrancaria o histórico financeiro de quem assinou."""
        from apps.plans.services import subscribe

        subscribe(client=client_profile, plan=plan, billing_type="PIX_MONTHLY")

        api = auth(owner)
        response = api.delete(f"/api/v1/plans/{plan.id}/")
        assert response.status_code == 400
        assert "PLAN_HAS_SUBSCRIBERS" in str(response.content, "utf-8")
        assert Plan.objects.filter(pk=plan.pk).exists()

    def test_plano_sem_assinante_e_excluido(self, auth, owner, plan):
        api = auth(owner)
        assert api.delete(f"/api/v1/plans/{plan.id}/").status_code == 204
        assert not PlanService.objects.filter(plan=plan).exists()

    def test_detalhe_expoe_o_que_o_formulario_edita(self, auth, owner, plan):
        """Mesma armadilha dos barbeiros: o form precisa do payload completo."""
        api = auth(owner)
        payload = data_of(api.get(f"/api/v1/plans/{plan.id}/"))
        for field in (
            "name",
            "description",
            "price",
            "overage_discount_percentage",
            "pays_barber_commission",
            "branch_ids",
            "plan_services",
        ):
            assert field in payload, f"o detalhe precisa expor '{field}'"
