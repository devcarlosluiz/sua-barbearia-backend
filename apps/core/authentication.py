"""Autenticação JWT com propagação do usuário para o contexto de auditoria."""

from __future__ import annotations

from typing import Any

from rest_framework_simplejwt.authentication import JWTAuthentication as BaseJWTAuthentication

from apps.core.context import set_current_user


class JWTAuthentication(BaseJWTAuthentication):
    """Igual ao SimpleJWT, mas registra o usuário no contextvar da requisição."""

    def authenticate(self, request: Any):  # type: ignore[override]
        result = super().authenticate(request)
        if result is not None:
            set_current_user(result[0])
        return result
