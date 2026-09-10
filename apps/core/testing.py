"""Auxiliares de teste para lidar com o envelope padrão da API.

`response.data` do DRF contém o payload antes da renderização; o envelope
(`success`/`data`/`message`/`errors`) só existe no corpo renderizado. Estes
helpers leem sempre o corpo final, garantindo que os testes validem o mesmo
JSON que o Flutter recebe.
"""

from __future__ import annotations

from typing import Any


def envelope(response: Any) -> dict[str, Any]:
    """Corpo completo da resposta, já renderizado."""
    return response.json()


def data_of(response: Any) -> Any:
    """Conteúdo de `data` no envelope."""
    return response.json()["data"]


def results_of(response: Any) -> list[Any]:
    """Itens de uma listagem paginada."""
    return response.json()["data"]["results"]


def errors_of(response: Any) -> dict[str, Any]:
    return response.json().get("errors") or {}


def code_of(response: Any) -> str | None:
    return response.json().get("code")


def expire_cancellation_window(appointment: Any) -> None:
    """Coloca o agendamento fora do prazo de cancelamento pelo aplicativo.

    Alarga a antecedência exigida pela filial em vez de empurrar o horário para
    perto de agora. Mexer no relógio parecia mais direto, mas amarrava o teste
    à hora em que ele roda: perto da meia-noite, `agora + 30 min` jogava o fim
    do atendimento para o dia seguinte e o banco recusava pela constraint
    `appointment_end_after_start`. Assim o resultado não depende do relógio.
    """
    branch = appointment.branch
    branch.cancellation_limit_hours = 24 * 365
    branch.save(update_fields=["cancellation_limit_hours"])
    appointment.refresh_from_db()
