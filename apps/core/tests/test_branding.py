"""Identidade visual: logo do sistema.

Dois pontos merecem cuidado e por isso estão travados aqui:

* a leitura é **pública** — sem isso a tela de login fica sem logo, já que ela
  é desenhada antes de existir sessão;
* a escrita é **só do proprietário** — a logo aparece para todo mundo, então
  trocá-la não pode ser possível a um cliente ou barbeiro.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.core.models import Branding
from apps.core.serializers import LOGO_MAX_BYTES, LOGO_STORED_MAX_HEIGHT
from apps.core.testing import data_of

pytestmark = pytest.mark.django_db

URL = "/api/v1/branding/"


def imagem(largura: int = 600, altura: int = 160, formato: str = "PNG") -> SimpleUploadedFile:
    """Gera um arquivo de imagem válido para upload."""
    buffer = BytesIO()
    # JPEG não tem canal alfa; gerar RGBA e salvar como JPEG falharia.
    modo, cor = ("RGB", (200, 160, 60)) if formato == "JPEG" else ("RGBA", (200, 160, 60, 255))
    Image.new(modo, (largura, altura), cor).save(buffer, format=formato)
    buffer.seek(0)
    extensao = "png" if formato == "PNG" else formato.lower()
    return SimpleUploadedFile(f"logo.{extensao}", buffer.read(), content_type=f"image/{extensao}")


@pytest.fixture(autouse=True)
def _limpa_logo(settings, tmp_path):
    """Cada teste escreve em um diretório de mídia próprio."""
    settings.MEDIA_ROOT = tmp_path
    yield


class TestLeituraPublica:
    def test_sem_sessao_devolve_a_marca_padrao(self, api: APIClient):
        payload = data_of(api.get(URL))

        assert payload["logo_url"] is None
        # O app precisa saber o formato esperado para orientar o proprietário.
        assert payload["recommended_width"] == 600
        assert payload["recommended_height"] == 160
        assert payload["max_file_size_mb"] == 2

    def test_sem_sessao_devolve_a_logo_enviada(self, api: APIClient, auth, owner):
        auth(owner).put(URL, {"logo": imagem()}, format="multipart")

        anonimo = APIClient()
        payload = data_of(anonimo.get(URL))
        assert payload["logo_url"] is not None
        assert "/media/branding/" in payload["logo_url"]

    def test_token_invalido_nao_quebra_a_leitura(self, api: APIClient):
        """A tela de login usa este endpoint com uma sessão possivelmente morta."""
        api.credentials(HTTP_AUTHORIZATION="Bearer token-invalido")
        response = api.get(URL)
        assert response.status_code == 200


class TestEscritaRestrita:
    def test_owner_envia_a_logo(self, auth, owner):
        response = auth(owner).put(URL, {"logo": imagem()}, format="multipart")
        assert response.status_code == 200, response.content

        branding = Branding.load()
        assert branding.logo
        assert branding.updated_by == owner

    def test_cliente_nao_troca_a_logo(self, auth, client_profile):
        response = auth(client_profile.user).put(URL, {"logo": imagem()}, format="multipart")
        assert response.status_code == 403
        assert not Branding.load().logo

    def test_barbeiro_nao_troca_a_logo(self, auth, barber):
        response = auth(barber.user).put(URL, {"logo": imagem()}, format="multipart")
        assert response.status_code == 403

    def test_anonimo_nao_troca_a_logo(self, api: APIClient):
        response = api.put(URL, {"logo": imagem()}, format="multipart")
        assert response.status_code in (401, 403)
        assert not Branding.load().logo


class TestValidacaoDoArquivo:
    def test_recusa_arquivo_grande_demais(self, auth, owner):
        gordo = SimpleUploadedFile(
            "logo.png", b"x" * (LOGO_MAX_BYTES + 1), content_type="image/png"
        )
        response = auth(owner).put(URL, {"logo": gordo}, format="multipart")
        # A mensagem precisa falar de tamanho, não "imagem inválida".

        assert response.status_code == 400
        assert "MB" in str(response.content, "utf-8")

    def test_recusa_imagem_pequena_demais(self, auth, owner):
        response = auth(owner).put(URL, {"logo": imagem(60, 20)}, format="multipart")

        assert response.status_code == 400
        # A mensagem diz o tamanho enviado e o mínimo — "arquivo inválido" não ajuda.
        corpo = str(response.content, "utf-8")
        assert "60x20" in corpo and "120x40" in corpo

    def test_recusa_arquivo_que_nao_e_imagem(self, auth, owner):
        falso = SimpleUploadedFile("logo.png", b"isto nao e uma imagem", content_type="image/png")
        response = auth(owner).put(URL, {"logo": falso}, format="multipart")
        assert response.status_code == 400

    def test_aceita_jpeg(self, auth, owner):
        response = auth(owner).put(URL, {"logo": imagem(formato="JPEG")}, format="multipart")
        assert response.status_code == 200, response.content


class TestNormalizacao:
    def test_reduz_a_altura_para_o_limite(self, auth, owner):
        """A logo nunca é desenhada com mais de 52 px: guardar 1200 é peso morto."""
        auth(owner).put(URL, {"logo": imagem(3000, 1200)}, format="multipart")

        branding = Branding.load()
        with Image.open(branding.logo.path) as saida:
            assert saida.height == LOGO_STORED_MAX_HEIGHT
            # A proporção original (2.5:1) é preservada.
            assert saida.width == pytest.approx(LOGO_STORED_MAX_HEIGHT * 2.5, abs=1)

    def test_nao_amplia_logo_pequena(self, auth, owner):
        auth(owner).put(URL, {"logo": imagem(300, 80)}, format="multipart")

        with Image.open(Branding.load().logo.path) as saida:
            assert (saida.width, saida.height) == (300, 80)

    def test_apara_a_margem_transparente(self, auth, owner):
        """Editores exportam a logo centralizada numa tela maior.

        Sem aparar, a margem vazia entra na altura fixa de desenho e a logo
        aparece menor do que o espaço disponível.
        """
        buffer = BytesIO()
        tela = Image.new("RGBA", (800, 400), (0, 0, 0, 0))
        # Desenho de 600x200 centralizado, com 100 px de margem em cima e embaixo.
        tela.paste(Image.new("RGBA", (600, 200), (200, 160, 60, 255)), (100, 100))
        tela.save(buffer, format="PNG")
        buffer.seek(0)
        arte = SimpleUploadedFile("logo.png", buffer.read(), content_type="image/png")

        auth(owner).put(URL, {"logo": arte}, format="multipart")

        with Image.open(Branding.load().logo.path) as saida:
            assert (saida.width, saida.height) == (600, 200)

    def test_imagem_sem_margem_fica_intacta(self, auth, owner):
        auth(owner).put(URL, {"logo": imagem(600, 160)}, format="multipart")

        with Image.open(Branding.load().logo.path) as saida:
            assert (saida.width, saida.height) == (600, 160)

    def test_converte_para_png_preservando_transparencia(self, auth, owner):
        auth(owner).put(URL, {"logo": imagem(formato="JPEG")}, format="multipart")

        with Image.open(Branding.load().logo.path) as saida:
            assert saida.format == "PNG"
            assert saida.mode in ("RGBA", "LA")


class TestNomeDaBarbearia:
    """O nome aparece na marca do app e no título da página."""

    def test_padrao_e_sua_barbearia(self, api: APIClient):
        assert data_of(api.get(URL))["company_name"] == "Sua Barbearia"

    def test_owner_altera_o_nome(self, auth, owner):
        response = auth(owner).patch(URL, {"company_name": "Barbearia Central"}, format="json")
        assert response.status_code == 200, response.content
        assert data_of(response)["company_name"] == "Barbearia Central"
        assert Branding.load().updated_by == owner

    def test_o_nome_novo_vale_para_quem_nao_esta_logado(self, auth, owner):
        """A tela de login mostra o nome antes de existir sessão."""
        auth(owner).patch(URL, {"company_name": "Barbearia do Zé"}, format="json")

        payload = data_of(APIClient().get(URL))
        assert payload["company_name"] == "Barbearia do Zé"

    def test_espacos_em_volta_sao_removidos(self, auth, owner):
        response = auth(owner).patch(URL, {"company_name": "   Corte Fino   "}, format="json")
        assert data_of(response)["company_name"] == "Corte Fino"

    def test_nome_muito_curto_e_recusado(self, auth, owner):
        response = auth(owner).patch(URL, {"company_name": "A"}, format="json")
        assert response.status_code == 400

    def test_nome_vazio_e_recusado(self, auth, owner):
        response = auth(owner).patch(URL, {"company_name": "   "}, format="json")
        assert response.status_code == 400
        assert Branding.load().company_name == "Sua Barbearia"

    def test_cliente_nao_altera_o_nome(self, auth, client_profile):
        response = auth(client_profile.user).patch(URL, {"company_name": "Invadida"}, format="json")
        assert response.status_code == 403
        assert Branding.load().company_name == "Sua Barbearia"

    def test_trocar_a_logo_nao_apaga_o_nome(self, auth, owner):
        api = auth(owner)
        api.patch(URL, {"company_name": "Barbearia Central"}, format="json")
        api.put(URL, {"logo": imagem()}, format="multipart")

        assert Branding.load().company_name == "Barbearia Central"

    def test_remover_a_logo_nao_apaga_o_nome(self, auth, owner):
        api = auth(owner)
        api.patch(URL, {"company_name": "Barbearia Central"}, format="json")
        api.put(URL, {"logo": imagem()}, format="multipart")
        api.delete(URL)

        assert Branding.load().company_name == "Barbearia Central"


class TestVoltarAoPadrao:
    def test_owner_remove_a_logo(self, auth, owner):
        api = auth(owner)
        api.put(URL, {"logo": imagem()}, format="multipart")

        response = api.delete(URL)
        assert response.status_code == 200, response.content
        assert data_of(response)["logo_url"] is None
        assert not Branding.load().logo

    def test_cliente_nao_remove_a_logo(self, auth, owner, client_profile):
        auth(owner).put(URL, {"logo": imagem()}, format="multipart")

        response = auth(client_profile.user).delete(URL)
        assert response.status_code == 403
        assert Branding.load().logo


class TestSingleton:
    def test_so_existe_um_registro(self, auth, owner):
        api = auth(owner)
        api.put(URL, {"logo": imagem()}, format="multipart")
        api.put(URL, {"logo": imagem(400, 120)}, format="multipart")

        assert Branding.objects.count() == 1

    def test_trocar_a_logo_apaga_o_arquivo_antigo(self, auth, owner):
        api = auth(owner)
        api.put(URL, {"logo": imagem()}, format="multipart")
        antigo = Branding.load().logo.path

        api.put(URL, {"logo": imagem(400, 120)}, format="multipart")

        import os

        assert not os.path.exists(antigo), "o arquivo anterior ficou ocupando disco"
