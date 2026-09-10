"""Testes da API de barbeiros.

Regressão de um incidente real: a tela de edição do proprietário era preenchida
com o payload da *listagem*, que não traz e-mail, telefone, comissão nem os
serviços. O formulário abria com os campos vazios e um "salvar" zerava a
comissão do barbeiro e sobrescrevia o nome com o apelido.

Os testes abaixo travam o contrato: o detalhe precisa expor tudo o que o
formulário edita, e uma atualização parcial não pode apagar o que não veio.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.barbers.models import Barber
from apps.core.testing import data_of, results_of

pytestmark = pytest.mark.django_db


@pytest.fixture
def named_barber(barber):
    """Barbeiro com apelido diferente do nome real — o caso que quebrava."""
    barber.user.first_name = "Kaue"
    barber.user.last_name = "Santos"
    barber.user.save(update_fields=["first_name", "last_name"])
    barber.nickname = "Kaue"
    barber.commission_percentage = Decimal("40.00")
    barber.bio = "Especialista em degradê."
    barber.save(update_fields=["nickname", "commission_percentage", "bio"])
    return barber


class TestDetalheDoBarbeiro:
    def test_detalhe_expoe_os_campos_editaveis(self, auth, owner, named_barber):
        api = auth(owner)
        payload = data_of(api.get(f"/api/v1/barbers/{named_barber.id}/"))

        # Sem estes campos, o formulário de edição abre vazio e destrói dados.
        for field in (
            "first_name",
            "last_name",
            "email",
            "phone",
            "commission_percentage",
            "bio",
            "barber_services",
            "branches",
        ):
            assert field in payload, f"o detalhe precisa expor '{field}'"

        assert payload["email"] == named_barber.user.email
        assert payload["commission_percentage"] == "40.00"

    def test_nome_real_nao_se_confunde_com_o_apelido(self, auth, owner, named_barber):
        api = auth(owner)
        payload = data_of(api.get(f"/api/v1/barbers/{named_barber.id}/"))

        # `name` é o nome de exibição (apelido); os campos reais são separados.
        assert payload["name"] == "Kaue"
        assert payload["first_name"] == "Kaue"
        assert payload["last_name"] == "Santos"

    def test_listagem_e_enxuta_de_proposito(self, auth, owner, named_barber):
        """A listagem é leve; por isso a edição precisa buscar o detalhe."""
        api = auth(owner)
        item = results_of(api.get("/api/v1/barbers/"))[0]

        assert "email" not in item
        assert "bio" not in item
        assert "barber_services" not in item


class TestAtualizacaoDoBarbeiro:
    def test_reenviar_o_proprio_email_nao_e_bloqueado(self, auth, owner, named_barber):
        """O validador de unicidade não pode rejeitar o e-mail do próprio usuário.

        O formulário de edição reenvia o bloco `user` inteiro; sem excluir o
        usuário atual da checagem, qualquer edição falhava com "Já existe uma
        conta com este e-mail".
        """
        api = auth(owner)
        response = api.patch(
            f"/api/v1/barbers/{named_barber.id}/",
            {
                "user": {
                    "first_name": "Kaue",
                    "last_name": "Santos",
                    "email": named_barber.user.email,
                    "phone": "",
                },
                "service_ids": [],
            },
            format="json",
        )
        assert response.status_code == 200, response.content

    def test_email_de_outro_usuario_continua_bloqueado(
        self, auth, owner, named_barber, client_profile
    ):
        api = auth(owner)
        response = api.patch(
            f"/api/v1/barbers/{named_barber.id}/",
            {
                "user": {
                    "first_name": "Kaue",
                    "email": client_profile.user.email,
                }
            },
            format="json",
        )
        assert response.status_code == 400
        assert "email" in str(response.content, "utf-8")

    def test_vincular_servicos_preserva_comissao_e_nome(self, auth, owner, named_barber, service):
        """Editar só os serviços não pode zerar comissão nem apagar o sobrenome."""
        api = auth(owner)
        response = api.patch(
            f"/api/v1/barbers/{named_barber.id}/",
            {"service_ids": [service.id]},
            format="json",
        )
        assert response.status_code == 200, response.content

        named_barber.refresh_from_db()
        named_barber.user.refresh_from_db()

        assert named_barber.commission_percentage == Decimal("40.00")
        assert named_barber.user.first_name == "Kaue"
        assert named_barber.user.last_name == "Santos"
        assert named_barber.bio == "Especialista em degradê."
        assert named_barber.barber_services.filter(service=service, is_active=True).exists()

    def test_atualizacao_nao_altera_o_email_de_acesso(self, auth, owner, named_barber):
        original = named_barber.user.email
        api = auth(owner)
        response = api.patch(
            f"/api/v1/barbers/{named_barber.id}/",
            {"user": {"first_name": "Kaue", "email": "outro@suabarbearia.com"}},
            format="json",
        )
        assert response.status_code == 200, response.content

        named_barber.user.refresh_from_db()
        assert named_barber.user.email == original

    def test_atualiza_comissao_quando_informada(self, auth, owner, named_barber):
        api = auth(owner)
        response = api.patch(
            f"/api/v1/barbers/{named_barber.id}/",
            {"commission_percentage": "55.00"},
            format="json",
        )
        assert response.status_code == 200, response.content

        named_barber.refresh_from_db()
        assert named_barber.commission_percentage == Decimal("55.00")

    def test_barbeiro_nao_edita_outro_barbeiro(self, auth, barber):
        api = auth(barber.user)
        response = api.patch(
            f"/api/v1/barbers/{barber.id}/",
            {"commission_percentage": "99.00"},
            format="json",
        )
        assert response.status_code == 403

        barber.refresh_from_db()
        assert barber.commission_percentage != Decimal("99.00")


class TestJornadaDeTrabalho:
    """A jornada define os horários oferecidos ao cliente.

    Regressão: o intervalo de almoço era validado contra o valor antigo quando
    o PATCH o enviava como nulo, e turnos fora do horário comercial ficavam
    impossíveis de cadastrar.
    """

    def _payload(self, barber, branch, **overrides):
        # A fixture já cobre segunda a sábado; domingo está livre para criar.
        payload = {
            "barber": barber.id,
            "branch": branch.id,
            "weekday": 6,
            "starts_at": "09:00:00",
            "ends_at": "18:00:00",
            "break_starts_at": "12:00:00",
            "break_ends_at": "13:00:00",
            "is_active": True,
        }
        payload.update(overrides)
        return payload

    def test_owner_cadastra_a_jornada(self, auth, owner, barber, branch):
        api = auth(owner)
        response = api.post("/api/v1/working-hours/", self._payload(barber, branch), format="json")
        assert response.status_code == 201, response.content
        assert barber.working_hours.filter(branch=branch, weekday=6).exists()

    def test_turno_da_tarde_sem_intervalo_e_aceito(self, auth, owner, barber, branch):
        """Um turno das 14h as 22h nao pode ser barrado por um intervalo inexistente."""
        api = auth(owner)
        response = api.post(
            "/api/v1/working-hours/",
            self._payload(
                barber,
                branch,
                starts_at="14:00:00",
                ends_at="22:00:00",
                break_starts_at=None,
                break_ends_at=None,
            ),
            format="json",
        )
        assert response.status_code == 201, response.content

    def test_remover_o_intervalo_ao_mudar_o_turno(self, auth, owner, barber, branch):
        api = auth(owner)
        hour_id = barber.working_hours.get(branch=branch, weekday=0).id

        # O intervalo antigo (12h as 13h) fica fora do novo expediente; enviá-lo
        # como nulo precisa limpá-lo em vez de ser validado contra o valor velho.
        response = api.patch(
            f"/api/v1/working-hours/{hour_id}/",
            {
                "starts_at": "14:00:00",
                "ends_at": "22:00:00",
                "break_starts_at": None,
                "break_ends_at": None,
            },
            format="json",
        )
        assert response.status_code == 200, response.content

        hour = barber.working_hours.get(pk=hour_id)
        assert hour.has_break is False
        assert hour.starts_at.hour == 14

    def test_intervalo_fora_do_expediente_continua_barrado(self, auth, owner, barber, branch):
        api = auth(owner)
        response = api.post(
            "/api/v1/working-hours/",
            self._payload(barber, branch, starts_at="14:00:00", ends_at="22:00:00"),
            format="json",
        )
        assert response.status_code == 400
        assert "break_starts_at" in str(response.content, "utf-8")

    def test_jornada_por_filial_nao_se_sobrepoe(self, auth, owner, barber, branch):
        """A jornada é única por (barbeiro, filial, dia) — duas filiais, duas jornadas."""
        from apps.branches.models import Branch

        other = Branch.objects.create(name="Unidade 2", city="Curitiba", state="PR")
        barber.branches.add(other)

        api = auth(owner)
        # Segunda-feira já existe na filial da fixture; a mesma segunda na outra
        # unidade é uma jornada distinta e não pode colidir.
        response = api.post(
            "/api/v1/working-hours/",
            self._payload(
                barber,
                other,
                weekday=0,
                starts_at="14:00:00",
                ends_at="20:00:00",
                break_starts_at=None,
                break_ends_at=None,
            ),
            format="json",
        )
        assert response.status_code == 201, response.content
        assert barber.working_hours.filter(weekday=0).count() == 2

    def test_filial_sem_vinculo_e_rejeitada(self, auth, owner, barber):
        from apps.branches.models import Branch

        alheia = Branch.objects.create(name="Outra rede", city="Curitiba", state="PR")

        api = auth(owner)
        response = api.post("/api/v1/working-hours/", self._payload(barber, alheia), format="json")
        assert response.status_code == 400
        assert "branch" in str(response.content, "utf-8")


class TestServicosDoBarbeiro:
    def test_endpoint_lista_os_servicos_ativos(self, auth, owner, barber, service):
        api = auth(owner)
        payload = data_of(api.get(f"/api/v1/barbers/{barber.id}/services/"))

        assert [item["service"] for item in payload] == [service.id]
        assert payload[0]["service_detail"]["name"] == service.name

    def test_desvincular_servico_o_marca_como_inativo(self, auth, owner, barber, service):
        api = auth(owner)
        response = api.patch(
            f"/api/v1/barbers/{barber.id}/",
            {"service_ids": []},
            format="json",
        )
        assert response.status_code == 200, response.content

        link = barber.barber_services.get(service=service)
        assert link.is_active is False

    def test_barbeiro_sem_o_servico_nao_aparece_no_filtro(self, auth, owner, barber, service):
        api = auth(owner)
        api.patch(f"/api/v1/barbers/{barber.id}/", {"service_ids": []}, format="json")

        results = results_of(api.get(f"/api/v1/barbers/?service={service.id}"))
        assert Barber.objects.count() == 1
        assert results == []
