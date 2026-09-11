"""Testes do "Entrar com o Google".

Dois níveis, de propósito:

* `TestGoogleIdTokenVerification` assina tokens de verdade com uma chave RSA
  criada no próprio teste e substitui só o JWKS — a validação de assinatura,
  `aud`, `exp` e algoritmo roda igual à de produção.
* `TestGoogleAuthEndpoint` troca o `_decode_id_token` por um dublê e se
  concentra nas regras de conta (criar, vincular, recusar).
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings

from apps.accounts import google as google_auth
from apps.accounts.models import User, UserRole
from apps.accounts.serializers import GoogleAuthSerializer
from apps.clients.models import Client
from apps.core.testing import code_of, data_of, errors_of

pytestmark = pytest.mark.django_db

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
GOOGLE_SUB = "111122223333444455556"


def base_claims(**overrides: Any) -> dict[str, Any]:
    agora = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": GOOGLE_SUB,
        "email": "joana@gmail.com",
        "email_verified": True,
        "given_name": "Joana",
        "family_name": "Souza",
        "iat": agora,
        "exp": agora + 3600,
    }
    claims.update(overrides)
    return claims


@pytest.fixture
def google_claims(monkeypatch) -> Any:
    """Dispensa a verificação criptográfica, mantendo as claims sob controle."""
    estado: dict[str, Any] = {"claims": base_claims()}

    def fake_decode(raw_token: str, client_ids: list[str]) -> dict[str, Any]:
        if raw_token == "token-invalido":
            raise google_auth.GoogleAuthError()
        return estado["claims"]

    monkeypatch.setattr(google_auth, "_decode_id_token", fake_decode)

    def definir(**overrides: Any) -> None:
        estado["claims"] = base_claims(**overrides)

    return definir


def post_google(api, **payload: Any):
    body = {"id_token": "token-do-google"}
    body.update(payload)
    return api.post("/api/v1/auth/google/", body, format="json")


# ---------------------------------------------------------------------------
# Verificação do token
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def fake_jwks(monkeypatch, rsa_key):
    """Devolve a chave pública do teste no lugar do JWKS do Google."""

    class FakeSigningKey:
        key = rsa_key.public_key()

    class FakeJWKSClient:
        def get_signing_key_from_jwt(self, token: str) -> FakeSigningKey:
            return FakeSigningKey()

    monkeypatch.setattr(google_auth, "_get_jwks_client", FakeJWKSClient)


def sign(claims: dict[str, Any], key: rsa.RSAPrivateKey) -> str:
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.mark.usefixtures("fake_jwks")
class TestGoogleIdTokenVerification:
    def test_token_valido_devolve_a_identidade(self, rsa_key):
        identity = google_auth.verify_google_id_token(sign(base_claims(), rsa_key))
        assert identity.sub == GOOGLE_SUB
        assert identity.email == "joana@gmail.com"
        assert identity.first_name == "Joana"
        assert identity.last_name == "Souza"

    def test_token_de_outro_aplicativo_e_recusado(self, rsa_key):
        token = sign(base_claims(aud="app-de-outra-empresa.apps.googleusercontent.com"), rsa_key)
        with pytest.raises(google_auth.GoogleAuthError):
            google_auth.verify_google_id_token(token)

    def test_token_expirado_e_recusado(self, rsa_key):
        agora = int(time.time())
        token = sign(base_claims(iat=agora - 7200, exp=agora - 3600), rsa_key)
        with pytest.raises(google_auth.GoogleAuthError):
            google_auth.verify_google_id_token(token)

    def test_token_assinado_por_outra_chave_e_recusado(self, rsa_key):
        intrusa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(google_auth.GoogleAuthError):
            google_auth.verify_google_id_token(sign(base_claims(), intrusa))

    def test_token_sem_assinatura_e_recusado(self, rsa_key):
        # `alg: none` é a tentativa clássica de burlar a verificação.
        token = jwt.encode(base_claims(), key=None, algorithm="none")
        with pytest.raises(google_auth.GoogleAuthError):
            google_auth.verify_google_id_token(token)

    def test_emissor_diferente_do_google_e_recusado(self, rsa_key):
        token = sign(base_claims(iss="https://accounts.exemplo.com"), rsa_key)
        with pytest.raises(google_auth.GoogleAuthError):
            google_auth.verify_google_id_token(token)

    def test_nome_ausente_usa_o_inicio_do_email(self, rsa_key):
        token = sign(base_claims(given_name=None, family_name=None, name=None), rsa_key)
        identity = google_auth.verify_google_id_token(token)
        assert identity.first_name == "joana"
        assert identity.last_name == ""

    @override_settings(GOOGLE_OAUTH_CLIENT_IDS=[])
    def test_sem_client_id_configurado_o_login_nao_e_oferecido(self, rsa_key):
        with pytest.raises(google_auth.GoogleAuthNotConfigured):
            google_auth.verify_google_id_token(sign(base_claims(), rsa_key))


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
class TestGoogleAuthEndpoint:
    def test_primeiro_acesso_cria_cliente_sem_senha_e_sem_data_de_nascimento(
        self, api, google_claims, branch
    ):
        response = post_google(api, preferred_branch_id=branch.id)
        assert response.status_code == 201, response.json()

        data = data_of(response)
        assert data["access"] and data["refresh"]
        assert data["created"] is True
        assert data["user"]["role"] == UserRole.CLIENT
        assert data["user"]["has_google_account"] is True
        assert data["user"]["has_password"] is False

        user = User.objects.get(email="joana@gmail.com")
        assert user.google_id == GOOGLE_SUB
        assert user.is_verified is True
        assert not user.has_usable_password()

        profile = Client.objects.get(user=user)
        assert profile.birth_date is None
        assert profile.preferred_branch == branch

    def test_acesso_seguinte_faz_login_sem_duplicar_a_conta(self, api, google_claims):
        assert post_google(api).status_code == 201
        response = post_google(api)

        assert response.status_code == 200
        assert data_of(response)["created"] is False
        assert User.objects.filter(email="joana@gmail.com").count() == 1
        assert Client.objects.filter(user__email="joana@gmail.com").count() == 1

    def test_conta_criada_por_requisicao_concorrente_e_reaproveitada(
        self, api, google_claims, monkeypatch
    ):
        # Dois toques no botão chegam juntos: quando o segundo vai criar, o
        # primeiro já criou. Em vez de erro, ele entra na conta existente.
        concorrente = User.objects.create_user(
            email="joana@gmail.com",
            password=None,
            first_name="Joana",
            role=UserRole.CLIENT,
            google_id=GOOGLE_SUB,
            is_verified=True,
        )
        Client.objects.create(user=concorrente)

        buscas = {"total": 0}
        busca_original = GoogleAuthSerializer._find_user

        def busca_com_corrida(self, identity):
            buscas["total"] += 1
            # Na primeira busca a conta "ainda não existia".
            return None if buscas["total"] == 1 else busca_original(self, identity)

        monkeypatch.setattr(GoogleAuthSerializer, "_find_user", busca_com_corrida)

        response = post_google(api)
        assert response.status_code == 200
        assert data_of(response)["created"] is False
        assert User.objects.filter(email="joana@gmail.com").count() == 1

    def test_email_que_mudou_no_google_nao_cria_conta_nova(self, api, google_claims):
        assert post_google(api).status_code == 201

        google_claims(email="joana.souza@gmail.com")
        response = post_google(api)

        assert response.status_code == 200
        # O vínculo é pelo `sub`, então continua sendo a mesma conta.
        assert User.objects.count() == 1

    def test_conta_existente_com_senha_e_vinculada_ao_google(self, api, google_claims, branch):
        existente = User.objects.create_user(
            email="joana@gmail.com",
            password="SenhaTeste@2026",
            first_name="Joana",
            role=UserRole.CLIENT,
        )
        Client.objects.create(user=existente, preferred_branch=branch)

        response = post_google(api)
        assert response.status_code == 200
        assert data_of(response)["created"] is False

        existente.refresh_from_db()
        assert existente.google_id == GOOGLE_SUB
        assert existente.is_verified is True
        # A senha antiga continua valendo: o cliente pode entrar dos dois jeitos.
        assert existente.has_usable_password()
        assert data_of(response)["user"]["has_password"] is True

    def test_barbeiro_entra_pelo_google_sem_ganhar_perfil_de_cliente(
        self, api, google_claims, barber
    ):
        google_claims(email=barber.user.email)
        response = post_google(api)

        assert response.status_code == 200
        assert data_of(response)["user"]["role"] == UserRole.BARBER
        assert not Client.objects.filter(user=barber.user).exists()

    def test_email_nao_verificado_no_google_e_recusado(self, api, google_claims):
        google_claims(email_verified=False)
        response = post_google(api)

        assert response.status_code == 401
        assert code_of(response) == "INVALID_GOOGLE_TOKEN"
        assert not User.objects.filter(email="joana@gmail.com").exists()

    def test_token_invalido_e_recusado(self, api, google_claims):
        response = api.post("/api/v1/auth/google/", {"id_token": "token-invalido"}, format="json")
        assert response.status_code == 401
        assert code_of(response) == "INVALID_GOOGLE_TOKEN"

    def test_id_token_e_obrigatorio(self, api, google_claims):
        response = api.post("/api/v1/auth/google/", {}, format="json")
        assert response.status_code == 400
        assert "id_token" in errors_of(response)

    def test_conta_inativa_nao_entra(self, api, google_claims, client_profile):
        client_profile.user.is_active = False
        client_profile.user.save(update_fields=["is_active"])
        google_claims(email=client_profile.user.email)

        response = post_google(api)
        assert response.status_code == 403
        assert code_of(response) == "ACCOUNT_INACTIVE"

    def test_filial_inexistente_falha_sem_criar_a_conta(self, api, google_claims):
        response = post_google(api, preferred_branch_id=9999)
        assert response.status_code == 400
        assert "preferred_branch_id" in errors_of(response)
        assert not User.objects.filter(email="joana@gmail.com").exists()

    @override_settings(GOOGLE_OAUTH_CLIENT_IDS=[])
    def test_sem_configuracao_responde_503(self, api, google_claims):
        response = post_google(api)
        assert response.status_code == 503
        assert code_of(response) == "GOOGLE_AUTH_NOT_CONFIGURED"
