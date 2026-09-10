"""Contexto por requisição (request id e usuário atual) usando contextvars.

Thread-safe e compatível com async; usado por logging e auditoria.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("suabarbearia_request_id", default=None)
_current_user: ContextVar[Any | None] = ContextVar("suabarbearia_current_user", default=None)
_client_ip: ContextVar[str | None] = ContextVar("suabarbearia_client_ip", default=None)
_user_agent: ContextVar[str | None] = ContextVar("suabarbearia_user_agent", default=None)


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_current_user(user: Any | None) -> None:
    _current_user.set(user)


def get_current_user() -> Any | None:
    user = _current_user.get()
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def set_request_meta(ip: str | None, user_agent: str | None) -> None:
    _client_ip.set(ip)
    _user_agent.set(user_agent)


def get_client_ip() -> str | None:
    return _client_ip.get()


def get_user_agent() -> str | None:
    return _user_agent.get()
