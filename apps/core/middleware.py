"""Middlewares da Sua Barbearia."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.core.context import set_current_user, set_request_id, set_request_meta

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
REQUEST_ID_RESPONSE_HEADER = "X-Request-ID"


class RequestIDMiddleware:
    """Garante um `X-Request-ID` por requisição para rastreabilidade."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.META.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.request_id = request_id
        set_request_id(request_id)
        set_request_meta(_client_ip(request), request.META.get("HTTP_USER_AGENT", "")[:400])
        try:
            response = self.get_response(request)
        finally:
            set_request_id(None)
        response[REQUEST_ID_RESPONSE_HEADER] = request_id
        return response


class CurrentUserMiddleware:
    """Expõe o usuário autenticado para a camada de auditoria."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        set_current_user(getattr(request, "user", None))
        try:
            return self.get_response(request)
        finally:
            set_current_user(None)


def _client_ip(request: HttpRequest) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
