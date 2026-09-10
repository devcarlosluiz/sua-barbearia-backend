"""Renderer que padroniza o envelope de resposta da API."""

from __future__ import annotations

from typing import Any

from rest_framework.renderers import JSONRenderer

ENVELOPE_KEYS = {"success", "data", "message", "errors"}


class EnvelopeJSONRenderer(JSONRenderer):
    """Envolve toda resposta em `{success, data, message, errors}`.

    Respostas já enveloped (produzidas pelo exception handler ou por views que
    montam o envelope manualmente) passam sem alteração.
    """

    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: dict[str, Any] | None = None,
    ) -> bytes:
        renderer_context = renderer_context or {}
        response = renderer_context.get("response")

        if response is not None and not _is_enveloped(data):
            if response.status_code >= 400:
                payload: dict[str, Any] = {
                    "success": False,
                    "data": None,
                    "message": _extract_message(data),
                    "code": "ERROR",
                    "errors": data if isinstance(data, dict) else {"detail": data},
                }
            else:
                payload = {
                    "success": True,
                    "data": data,
                    "message": response.headers.get("X-Message") or None,
                    "errors": None,
                }
            data = payload

        return super().render(data, accepted_media_type, renderer_context)


def _is_enveloped(data: Any) -> bool:
    return isinstance(data, dict) and ENVELOPE_KEYS.issubset(set(data.keys()))


def _extract_message(data: Any) -> str:
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
    if isinstance(data, str):
        return data
    return "Não foi possível realizar a operação."
