"""Verificação do ID token do "Entrar com o Google".

O fluxo é todo do lado do app: o Flutter (ou a web) abre o Sign in with Google,
recebe um **ID token** JWT assinado pelo Google e manda ele para
`POST /api/v1/auth/google/`. Este módulo é quem decide se aquele token é
legítimo antes de qualquer coisa virar usuário no banco.

A verificação é local, com a chave pública do Google:

* a assinatura RS256 é conferida contra o JWKS publicado em `CERTS_URL`
  (o `PyJWKClient` mantém as chaves em memória, então o login normal não faz
  chamada de rede nenhuma);
* `iss` precisa ser o Google;
* `aud` precisa ser um dos nossos client IDs — sem isso, um token emitido para
  *qualquer outro* aplicativo Google serviria para entrar aqui;
* `exp`/`iat` são validados pelo próprio PyJWT.

O endpoint `tokeninfo` do Google resolveria o mesmo, mas cobraria uma ida à
rede por login e é documentado pelo Google como ferramenta de depuração.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import jwt
from django.conf import settings

from apps.core.exceptions import BusinessError

logger = logging.getLogger("suabarbearia.security")

#: Chaves públicas de assinatura dos ID tokens.
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"

#: O Google emite os dois formatos; ambos são válidos.
ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})

#: Tolerância para relógios fora de sincronia entre o Google e este servidor.
LEEWAY_SECONDS = 30

#: Por quanto tempo o JWKS fica em memória antes de ser buscado de novo.
JWKS_LIFESPAN_SECONDS = 60 * 60

_jwks_client: jwt.PyJWKClient | None = None


class GoogleAuthNotConfigured(BusinessError):
    """Falta o client ID: o login com Google não pode ser oferecido."""

    def __init__(self) -> None:
        super().__init__(
            "O login com o Google não está configurado. Fale com a barbearia.",
            code="GOOGLE_AUTH_NOT_CONFIGURED",
            status_code=503,
        )


class GoogleAuthError(BusinessError):
    """Token ausente, expirado, adulterado ou emitido para outro app."""

    def __init__(self, message: str = "") -> None:
        super().__init__(
            message or "Não foi possível validar sua conta Google. Tente entrar novamente.",
            code="INVALID_GOOGLE_TOKEN",
            status_code=401,
        )


@dataclass(frozen=True)
class GoogleIdentity:
    """O que o Google afirma sobre a pessoa que acabou de entrar."""

    sub: str
    email: str
    first_name: str
    last_name: str
    email_verified: bool


def _client_ids() -> list[str]:
    ids = [value.strip() for value in settings.GOOGLE_OAUTH_CLIENT_IDS if value.strip()]
    if not ids:
        raise GoogleAuthNotConfigured()
    return ids


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(
            CERTS_URL,
            cache_keys=True,
            lifespan=JWKS_LIFESPAN_SECONDS,
            timeout=10,
        )
    return _jwks_client


def _decode_id_token(raw_token: str, client_ids: list[str]) -> dict[str, Any]:
    """Valida assinatura, `aud` e validade, devolvendo as claims do token."""
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(raw_token)
        return jwt.decode(
            raw_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_ids,
            leeway=LEEWAY_SECONDS,
            options={"require": ["aud", "exp", "iat", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise GoogleAuthError("Sua sessão do Google expirou. Entre novamente.") from exc
    except jwt.InvalidAudienceError as exc:
        # Token legítimo do Google, mas emitido para outro aplicativo.
        logger.warning("ID token do Google recusado: audiência inválida.")
        raise GoogleAuthError() from exc
    except jwt.PyJWTError as exc:
        logger.warning("ID token do Google recusado: %s", type(exc).__name__)
        raise GoogleAuthError() from exc
    except Exception as exc:  # falha ao buscar o JWKS, rede fora, etc.
        logger.exception("Falha ao verificar o ID token do Google.")
        raise BusinessError(
            "Não foi possível falar com o Google agora. Tente novamente em instantes.",
            code="GOOGLE_AUTH_UNAVAILABLE",
            status_code=503,
        ) from exc


def _split_name(claims: dict[str, Any], email: str) -> tuple[str, str]:
    """Primeiro e último nome, com fallback para o começo do e-mail.

    `first_name` é obrigatório no modelo, e nem toda conta Google devolve
    `given_name` — contas corporativas com perfil restrito, por exemplo.
    """
    first = (claims.get("given_name") or "").strip()
    last = (claims.get("family_name") or "").strip()
    if not first:
        partes = (claims.get("name") or "").strip().split()
        if partes:
            first, last = partes[0], (last or " ".join(partes[1:]))
        else:
            first = email.split("@")[0]
    return first[:80], last[:120]


def verify_google_id_token(raw_token: str) -> GoogleIdentity:
    """Verifica o ID token e devolve a identidade confirmada pelo Google."""
    claims = _decode_id_token(raw_token, _client_ids())

    if claims.get("iss") not in ISSUERS:
        logger.warning("ID token do Google recusado: emissor inesperado.")
        raise GoogleAuthError()

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleAuthError("Sua conta Google não compartilhou um e-mail.")

    # `email_verified` falso significa que o Google não provou que o e-mail é
    # desta pessoa — aceitar levaria a sequestro da conta homônima daqui.
    email_verified = bool(claims.get("email_verified"))
    if not email_verified:
        raise GoogleAuthError("O e-mail da sua conta Google ainda não foi verificado.")

    first_name, last_name = _split_name(claims, email)
    return GoogleIdentity(
        sub=str(claims["sub"]),
        email=email,
        first_name=first_name,
        last_name=last_name,
        email_verified=email_verified,
    )
