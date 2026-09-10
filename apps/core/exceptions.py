"""Exceções de negócio e handler padronizado da API."""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("suabarbearia.api")


class BusinessError(APIException):
    """Erro de regra de negócio com código estável consumido pelo frontend."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "BUSINESS_ERROR"
    default_detail = "Não foi possível realizar a operação."

    def __init__(
        self,
        detail: str | None = None,
        code: str | None = None,
        status_code: int | None = None,
        errors: dict[str, Any] | None = None,
    ) -> None:
        self.business_code = code or self.default_code
        self.errors = errors or {}
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail or self.default_detail, code=self.business_code)


class ConflictError(BusinessError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CONFLICT"


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Converte qualquer exceção no envelope padrão da API."""
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(detail=getattr(exc, "message_dict", None) or list(exc.messages))
    if isinstance(exc, PermissionDenied):
        exc = APIException(detail="Você não tem permissão para executar esta ação.")
        exc.status_code = status.HTTP_403_FORBIDDEN
    if isinstance(exc, Http404):
        # A mensagem padrão do Django vem em inglês; padronizamos em pt-BR.
        exc = NotFound(detail="Registro não encontrado.")

    response = drf_exception_handler(exc, context)

    if response is None:
        logger.exception("Erro nao tratado na API: %s", exc)
        return Response(
            {
                "success": False,
                "data": None,
                "message": "Erro interno do servidor. Tente novamente em instantes.",
                "code": "INTERNAL_ERROR",
                "errors": {},
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = getattr(exc, "business_code", None) or _default_code(exc, response.status_code)
    message = _build_message(exc, response.data)
    errors = getattr(exc, "errors", None) or _build_errors(response.data)

    response.data = {
        "success": False,
        "data": None,
        "message": message,
        "code": code,
        "errors": errors,
    }
    return response


def _default_code(exc: Exception, status_code: int) -> str:
    mapping = {
        status.HTTP_400_BAD_REQUEST: "VALIDATION_ERROR",
        status.HTTP_401_UNAUTHORIZED: "UNAUTHENTICATED",
        status.HTTP_403_FORBIDDEN: "PERMISSION_DENIED",
        status.HTTP_404_NOT_FOUND: "NOT_FOUND",
        status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
        status.HTTP_409_CONFLICT: "CONFLICT",
        status.HTTP_429_TOO_MANY_REQUESTS: "THROTTLED",
    }
    if isinstance(exc, Http404):
        return "NOT_FOUND"
    return mapping.get(status_code, "ERROR")


def _build_message(exc: Exception, data: Any) -> str:
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        return "Verifique os dados informados e tente novamente."
    if isinstance(data, list) and data and isinstance(data[0], str):
        return data[0]
    return str(getattr(exc, "detail", None) or "Não foi possível realizar a operação.")


def _build_errors(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k != "detail"}
    if isinstance(data, list):
        return {"detail": data}
    return {}
