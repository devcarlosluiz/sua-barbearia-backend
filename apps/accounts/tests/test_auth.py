"""Testes de autenticação."""

from __future__ import annotations

import pytest
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import PasswordResetToken, User, UserRole
from apps.clients.models import Client
from apps.core.testing import code_of, data_of, errors_of

pytestmark = pytest.mark.django_db

PASSWORD = "SenhaTeste@2026"


class TestLogin:
    def test_login_retorna_tokens_e_usuario(self, api, owner):
        response = api.post(
            "/api/v1/auth/login/", {"email": owner.email, "password": PASSWORD}, format="json"
        )
        assert response.status_code == 200
        data = data_of(response)
        assert data["access"]
        assert data["refresh"]
        assert data["user"]["email"] == owner.email
        assert data["user"]["role"] == UserRole.OWNER

    def test_login_com_senha_errada_retorna_401(self, api, owner):
        response = api.post(
            "/api/v1/auth/login/", {"email": owner.email, "password": "errada"}, format="json"
        )
        assert response.status_code == 401
        assert response.json()["success"] is False

    def test_login_de_usuario_inativo_e_bloqueado(self, api, owner):
        owner.is_active = False
        owner.save(update_fields=["is_active"])
        response = api.post(
            "/api/v1/auth/login/", {"email": owner.email, "password": PASSWORD}, format="json"
        )
        assert response.status_code == 401

    def test_email_e_case_insensitive(self, api, owner):
        response = api.post(
            "/api/v1/auth/login/",
            {"email": owner.email.upper(), "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 200


class TestRefresh:
    def test_refresh_gera_novo_access(self, api, owner):
        refresh = RefreshToken.for_user(owner)
        response = api.post("/api/v1/auth/refresh/", {"refresh": str(refresh)}, format="json")
        assert response.status_code == 200
        assert data_of(response)["access"]

    def test_refresh_invalido_retorna_401(self, api):
        response = api.post("/api/v1/auth/refresh/", {"refresh": "invalido"}, format="json")
        assert response.status_code == 401


class TestLogout:
    def test_logout_invalida_o_refresh(self, api, auth, owner):
        client = auth(owner)
        refresh = RefreshToken.for_user(owner)
        response = client.post("/api/v1/auth/logout/", {"refresh": str(refresh)}, format="json")
        assert response.status_code == 204

        reuse = api.post("/api/v1/auth/refresh/", {"refresh": str(refresh)}, format="json")
        assert reuse.status_code == 401

    def test_logout_exige_autenticacao(self, api):
        response = api.post("/api/v1/auth/logout/", {"refresh": "x"}, format="json")
        assert response.status_code == 401


class TestRegister:
    def test_registro_cria_usuario_cliente_e_perfil(self, api, branch):
        payload = {
            "first_name": "Novo",
            "last_name": "Cliente",
            "email": "novo@test.com",
            "phone": "41999998888",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "preferred_branch_id": branch.id,
        }
        response = api.post("/api/v1/auth/register/", payload, format="json")
        assert response.status_code == 201, response.data

        user = User.objects.get(email="novo@test.com")
        assert user.role == UserRole.CLIENT
        assert Client.objects.filter(user=user, preferred_branch=branch).exists()
        assert data_of(response)["access"]

    def test_registro_com_email_duplicado_falha(self, api, owner):
        payload = {
            "first_name": "Duplicado",
            "email": owner.email,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
        }
        response = api.post("/api/v1/auth/register/", payload, format="json")
        assert response.status_code == 400
        assert "email" in errors_of(response)

    def test_registro_com_senhas_diferentes_falha(self, api):
        payload = {
            "first_name": "Teste",
            "email": "outro@test.com",
            "password": PASSWORD,
            "password_confirm": "OutraSenha@2026",
        }
        response = api.post("/api/v1/auth/register/", payload, format="json")
        assert response.status_code == 400
        assert "password_confirm" in errors_of(response)

    def test_registro_nao_permite_escolher_papel(self, api):
        payload = {
            "first_name": "Malicioso",
            "email": "hacker@test.com",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "role": "OWNER",
        }
        response = api.post("/api/v1/auth/register/", payload, format="json")
        assert response.status_code == 201
        assert User.objects.get(email="hacker@test.com").role == UserRole.CLIENT


class TestMe:
    def test_me_retorna_usuario_e_perfil_do_cliente(self, auth, client_profile):
        client = auth(client_profile.user)
        response = client.get("/api/v1/auth/me/")
        assert response.status_code == 200
        assert data_of(response)["user"]["role"] == UserRole.CLIENT
        assert data_of(response)["client"]["id"] == client_profile.id

    def test_me_retorna_perfil_do_barbeiro(self, auth, barber):
        client = auth(barber.user)
        response = client.get("/api/v1/auth/me/")
        assert response.status_code == 200
        assert data_of(response)["barber"]["id"] == barber.id

    def test_me_sem_token_retorna_401(self, api):
        assert api.get("/api/v1/auth/me/").status_code == 401

    def test_me_atualiza_dados_do_proprio_usuario(self, auth, client_profile):
        client = auth(client_profile.user)
        response = client.patch("/api/v1/auth/me/", {"first_name": "Alterado"}, format="json")
        assert response.status_code == 200
        client_profile.user.refresh_from_db()
        assert client_profile.user.first_name == "Alterado"


class TestPasswordReset:
    def test_forgot_password_gera_token(self, api, owner, mailoutbox):
        response = api.post("/api/v1/auth/forgot-password/", {"email": owner.email}, format="json")
        assert response.status_code == 202
        assert PasswordResetToken.objects.filter(user=owner, used_at__isnull=True).exists()
        assert len(mailoutbox) == 1

    def test_forgot_password_nao_revela_email_inexistente(self, api):
        response = api.post(
            "/api/v1/auth/forgot-password/", {"email": "naoexiste@test.com"}, format="json"
        )
        assert response.status_code == 202

    def test_reset_password_troca_a_senha_e_consome_o_token(self, api, owner):
        token = PasswordResetToken.issue(owner)
        response = api.post(
            "/api/v1/auth/reset-password/",
            {"token": token.token, "new_password": "NovaSenha@2026"},
            format="json",
        )
        assert response.status_code == 204

        owner.refresh_from_db()
        assert owner.check_password("NovaSenha@2026")
        token.refresh_from_db()
        assert token.used_at is not None

    def test_token_usado_nao_funciona_novamente(self, api, owner):
        token = PasswordResetToken.issue(owner)
        api.post(
            "/api/v1/auth/reset-password/",
            {"token": token.token, "new_password": "NovaSenha@2026"},
            format="json",
        )
        response = api.post(
            "/api/v1/auth/reset-password/",
            {"token": token.token, "new_password": "OutraSenha@2026"},
            format="json",
        )
        assert response.status_code == 400
        assert code_of(response) == "INVALID_RESET_TOKEN"


class TestChangePassword:
    def test_troca_de_senha_com_senha_atual_correta(self, auth, owner):
        client = auth(owner)
        response = client.post(
            "/api/v1/auth/change-password/",
            {"current_password": PASSWORD, "new_password": "NovaSenha@2026"},
            format="json",
        )
        assert response.status_code == 204
        owner.refresh_from_db()
        assert owner.check_password("NovaSenha@2026")

    def test_troca_de_senha_com_senha_atual_errada_falha(self, auth, owner):
        client = auth(owner)
        response = client.post(
            "/api/v1/auth/change-password/",
            {"current_password": "errada", "new_password": "NovaSenha@2026"},
            format="json",
        )
        assert response.status_code == 400
