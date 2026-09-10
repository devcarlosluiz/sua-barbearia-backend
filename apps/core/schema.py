"""Extensões do drf-spectacular usadas pela documentação da API."""

from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class SuaBarbeariaJWTScheme(OpenApiAuthenticationExtension):
    """Ensina o drf-spectacular a documentar o nosso JWTAuthentication."""

    target_class = "apps.core.authentication.JWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "Envie o access token no header: `Authorization: Bearer <access>`. "
                "Obtenha o token em `POST /api/v1/auth/login/`."
            ),
        }
