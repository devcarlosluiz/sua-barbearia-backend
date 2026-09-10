"""Foto de perfil do usuário.

A foto chega do celular com vários MB e orientação em EXIF, e é exibida em
círculos de 38 a 56 px. O que se testa aqui é que ela seja aceita, normalizada
e trocada sem deixar lixo — e que ninguém mexa na foto de outra pessoa.
"""

from __future__ import annotations

import os
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.core.services.images import (
    AVATAR_MAX_BYTES,
    AVATAR_STORED_SIZE,
)
from apps.core.testing import data_of

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/me/"
URL_AVATAR = "/api/v1/auth/me/avatar/"


def foto(largura: int = 800, altura: int = 800, formato: str = "JPEG") -> SimpleUploadedFile:
    buffer = BytesIO()
    modo, cor = ("RGB", (120, 90, 60)) if formato == "JPEG" else ("RGBA", (120, 90, 60, 255))
    Image.new(modo, (largura, altura), cor).save(buffer, format=formato)
    buffer.seek(0)
    extensao = "jpg" if formato == "JPEG" else formato.lower()
    return SimpleUploadedFile(f"foto.{extensao}", buffer.read(), content_type=f"image/{extensao}")


@pytest.fixture(autouse=True)
def _midia_isolada(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    yield


class TestEnvio:
    def test_cliente_envia_a_propria_foto(self, auth, client_profile):
        api = auth(client_profile.user)
        response = api.patch(URL, {"avatar": foto()}, format="multipart")

        assert response.status_code == 200, response.content
        client_profile.user.refresh_from_db()
        assert client_profile.user.avatar

        # O app monta o avatar a partir desta URL.
        assert data_of(response)["user"]["avatar_url"] is not None

    def test_barbeiro_e_owner_tambem_podem(self, auth, barber, owner):
        for usuario in (barber.user, owner):
            response = auth(usuario).patch(URL, {"avatar": foto()}, format="multipart")
            assert response.status_code == 200, response.content
            usuario.refresh_from_db()
            assert usuario.avatar

    def test_anonimo_nao_envia(self, api: APIClient):
        response = api.patch(URL, {"avatar": foto()}, format="multipart")
        assert response.status_code == 401

    def test_enviar_a_foto_nao_apaga_o_nome(self, auth, client_profile):
        """`PATCH` parcial: mandar só a foto não pode limpar os outros campos."""
        usuario = client_profile.user
        auth(usuario).patch(URL, {"avatar": foto()}, format="multipart")

        usuario.refresh_from_db()
        assert usuario.first_name
        assert usuario.email


class TestNormalizacao:
    def test_vira_um_quadrado_do_tamanho_guardado(self, auth, client_profile):
        auth(client_profile.user).patch(URL, {"avatar": foto(2400, 1200)}, format="multipart")

        client_profile.user.refresh_from_db()
        with Image.open(client_profile.user.avatar.path) as saida:
            assert saida.size == (AVATAR_STORED_SIZE, AVATAR_STORED_SIZE)
            assert saida.format == "JPEG"

    def test_foto_grande_encolhe_bastante(self, auth, client_profile):
        """Uma foto de celular não pode ser baixada inteira a cada tela."""
        grande = foto(3000, 3000)
        tamanho_original = grande.size

        auth(client_profile.user).patch(URL, {"avatar": grande}, format="multipart")

        client_profile.user.refresh_from_db()
        assert client_profile.user.avatar.size < tamanho_original

    def test_png_com_transparencia_nao_vira_fundo_preto(self, auth, client_profile):
        buffer = BytesIO()
        Image.new("RGBA", (400, 400), (0, 0, 0, 0)).save(buffer, format="PNG")
        buffer.seek(0)
        transparente = SimpleUploadedFile("foto.png", buffer.read(), content_type="image/png")

        auth(client_profile.user).patch(URL, {"avatar": transparente}, format="multipart")

        client_profile.user.refresh_from_db()
        with Image.open(client_profile.user.avatar.path) as saida:
            assert saida.mode == "RGB"
            # A área transparente vira branco, não preto.
            assert saida.getpixel((10, 10)) == (255, 255, 255)

    def test_trocar_a_foto_apaga_o_arquivo_antigo(self, auth, client_profile):
        api = auth(client_profile.user)
        api.patch(URL, {"avatar": foto()}, format="multipart")
        client_profile.user.refresh_from_db()
        antigo = client_profile.user.avatar.path

        api.patch(URL, {"avatar": foto(600, 600)}, format="multipart")

        assert not os.path.exists(antigo), "o arquivo anterior ficou ocupando disco"

    def test_a_url_muda_a_cada_troca(self, auth, client_profile):
        """Com nome fixo, o navegador continuaria exibindo a foto antiga."""
        api = auth(client_profile.user)
        primeira = data_of(api.patch(URL, {"avatar": foto()}, format="multipart"))
        segunda = data_of(api.patch(URL, {"avatar": foto(600, 600)}, format="multipart"))

        assert primeira["user"]["avatar_url"] != segunda["user"]["avatar_url"]


class TestValidacao:
    def test_recusa_arquivo_grande_demais(self, auth, client_profile):
        gordo = SimpleUploadedFile(
            "foto.jpg", b"x" * (AVATAR_MAX_BYTES + 1), content_type="image/jpeg"
        )
        response = auth(client_profile.user).patch(URL, {"avatar": gordo}, format="multipart")

        assert response.status_code == 400
        assert "MB" in str(response.content, "utf-8")

    def test_recusa_imagem_minuscula(self, auth, client_profile):
        response = auth(client_profile.user).patch(
            URL, {"avatar": foto(40, 40)}, format="multipart"
        )

        assert response.status_code == 400
        assert "40x40" in str(response.content, "utf-8")

    def test_recusa_arquivo_que_nao_e_imagem(self, auth, client_profile):
        falso = SimpleUploadedFile("foto.jpg", b"isto nao e uma imagem", content_type="image/jpeg")
        response = auth(client_profile.user).patch(URL, {"avatar": falso}, format="multipart")
        assert response.status_code == 400

    def test_aceita_png_e_webp(self, auth, client_profile):
        api = auth(client_profile.user)
        for formato in ("PNG", "WEBP"):
            response = api.patch(URL, {"avatar": foto(formato=formato)}, format="multipart")
            assert response.status_code == 200, f"{formato}: {response.content}"


class TestRemocao:
    def test_usuario_remove_a_propria_foto(self, auth, client_profile):
        api = auth(client_profile.user)
        api.patch(URL, {"avatar": foto()}, format="multipart")
        client_profile.user.refresh_from_db()
        caminho = client_profile.user.avatar.path

        response = api.delete(URL_AVATAR)
        assert response.status_code == 200, response.content
        assert data_of(response)["avatar_url"] is None

        client_profile.user.refresh_from_db()
        assert not client_profile.user.avatar
        assert not os.path.exists(caminho)

    def test_remover_sem_foto_nao_quebra(self, auth, client_profile):
        response = auth(client_profile.user).delete(URL_AVATAR)
        assert response.status_code == 200

    def test_anonimo_nao_remove(self, api: APIClient):
        assert api.delete(URL_AVATAR).status_code == 401


class TestIsolamentoEntreUsuarios:
    def test_a_foto_de_um_nao_afeta_a_do_outro(self, auth, client_profile, other_client):
        auth(client_profile.user).patch(URL, {"avatar": foto()}, format="multipart")
        auth(other_client.user).patch(URL, {"avatar": foto(600, 600)}, format="multipart")

        client_profile.user.refresh_from_db()
        other_client.user.refresh_from_db()

        assert client_profile.user.avatar
        assert other_client.user.avatar
        assert client_profile.user.avatar.name != other_client.user.avatar.name

    def test_remover_a_propria_nao_apaga_a_de_outro(self, auth, client_profile, other_client):
        auth(client_profile.user).patch(URL, {"avatar": foto()}, format="multipart")
        auth(other_client.user).patch(URL, {"avatar": foto()}, format="multipart")

        auth(client_profile.user).delete(URL_AVATAR)

        other_client.user.refresh_from_db()
        assert other_client.user.avatar
        assert os.path.exists(other_client.user.avatar.path)
