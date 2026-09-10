"""Cliente HTTP do Mercado Pago.

Cobre só o que a Sua Barbearia usa:

* **PIX avulso** (`/v1/payments`) — a cobrança de cada ciclo no plano PIX.
* **Assinatura recorrente** (`/preapproval`) — o cartão de crédito. O cartão é
  digitado no checkout hospedado pelo Mercado Pago (`init_point`); nem o app
  nem este backend recebem número, CVV ou validade. Isso mantém os dados de
  cartão fora do nosso escopo de PCI.
* **Consulta** de pagamento/assinatura, usada pelo webhook e pela varredura de
  segurança que roda no Celery.

O `access_token` nunca é registrado em log: `_request` loga método, caminho e
status, e o corpo da resposta só entra em log de erro depois de passar por
`mask_sensitive`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

from apps.core.exceptions import BusinessError

logger = logging.getLogger("suabarbearia.application")

API_BASE_URL = "https://api.mercadopago.com"
DEFAULT_TIMEOUT = 20

#: Chaves que nunca devem aparecer em log, mesmo vindas do provedor.
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "card",
        "card_token",
        "card_number",
        "security_code",
        "expiration_month",
        "expiration_year",
        "token",
    }
)


def mask_sensitive(value: Any) -> Any:
    """Substitui por `***` qualquer chave sensível, em qualquer profundidade."""
    if isinstance(value, dict):
        return {
            key: ("***" if key.lower() in _SENSITIVE_KEYS else mask_sensitive(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    return value


class MercadoPagoNotConfigured(BusinessError):
    """Falta o access token: a assinatura online não pode ser oferecida."""

    def __init__(self) -> None:
        super().__init__(
            "O pagamento online não está configurado. Fale com a barbearia.",
            code="GATEWAY_NOT_CONFIGURED",
            status_code=503,
        )


class MercadoPagoError(BusinessError):
    """Erro devolvido pelo provedor, já traduzido para o cliente."""

    def __init__(self, message: str = "", *, code: str = "GATEWAY_ERROR") -> None:
        super().__init__(
            message or "Não foi possível falar com o Mercado Pago. Tente novamente.",
            code=code,
            status_code=502,
        )


@dataclass
class PixCharge:
    """Cobrança PIX pronta para ser exibida ao cliente."""

    external_id: str
    status: str
    qr_code: str
    qr_code_base64: str
    ticket_url: str
    expires_at: str | None
    payload: dict[str, Any]


@dataclass
class Preapproval:
    """Assinatura recorrente criada no provedor."""

    external_id: str
    status: str
    init_point: str
    payload: dict[str, Any]


class MercadoPagoClient:
    """Wrapper fino sobre a API REST do Mercado Pago."""

    def __init__(
        self,
        access_token: str | None = None,
        *,
        base_url: str = API_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._access_token = access_token or settings.MERCADO_PAGO_ACCESS_TOKEN
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self._access_token)

    # ------------------------------------------------------------------
    # PIX (cobrança avulsa de um ciclo)
    # ------------------------------------------------------------------
    def create_pix_payment(
        self,
        *,
        amount: Decimal,
        description: str,
        external_reference: str,
        payer_email: str,
        payer_first_name: str = "",
        payer_last_name: str = "",
        expires_in_minutes: int = 60,
        notification_url: str | None = None,
    ) -> PixCharge:
        payload: dict[str, Any] = {
            "transaction_amount": float(Decimal(amount)),
            "description": description,
            "payment_method_id": "pix",
            "external_reference": external_reference,
            "date_of_expiration": _iso_expiration(expires_in_minutes),
            "payer": {
                "email": payer_email,
                "first_name": payer_first_name or "Cliente",
                "last_name": payer_last_name or "Sua Barbearia",
            },
        }
        url = notification_url or settings.MERCADO_PAGO_NOTIFICATION_URL
        if url:
            payload["notification_url"] = url

        data = self._request("POST", "/v1/payments", json=payload, idempotent=True)
        transfer = (data.get("point_of_interaction") or {}).get("transaction_data") or {}
        return PixCharge(
            external_id=str(data.get("id", "")),
            status=str(data.get("status", "")),
            qr_code=transfer.get("qr_code", "") or "",
            qr_code_base64=transfer.get("qr_code_base64", "") or "",
            ticket_url=transfer.get("ticket_url", "") or "",
            expires_at=data.get("date_of_expiration"),
            payload=data,
        )

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/payments/{payment_id}")

    def refund_payment(self, payment_id: str, amount: Decimal | None = None) -> dict[str, Any]:
        body = {"amount": float(Decimal(amount))} if amount is not None else {}
        return self._request(
            "POST", f"/v1/payments/{payment_id}/refunds", json=body, idempotent=True
        )

    # ------------------------------------------------------------------
    # Assinatura recorrente no cartão
    # ------------------------------------------------------------------
    def create_preapproval(
        self,
        *,
        amount: Decimal,
        reason: str,
        external_reference: str,
        payer_email: str,
        back_url: str | None = None,
        notification_url: str | None = None,
    ) -> Preapproval:
        """Cria a assinatura em `pending`.

        O cliente precisa abrir o `init_point` para informar o cartão no
        ambiente do Mercado Pago. A confirmação chega por webhook.
        """
        payload: dict[str, Any] = {
            "reason": reason,
            "external_reference": external_reference,
            "payer_email": payer_email,
            "back_url": back_url or settings.MERCADO_PAGO_BACK_URL,
            "auto_recurring": {
                "frequency": 1,
                "frequency_type": "months",
                "transaction_amount": float(Decimal(amount)),
                "currency_id": "BRL",
            },
        }
        url = notification_url or settings.MERCADO_PAGO_NOTIFICATION_URL
        if url:
            payload["notification_url"] = url

        data = self._request("POST", "/preapproval", json=payload, idempotent=True)
        return Preapproval(
            external_id=str(data.get("id", "")),
            status=str(data.get("status", "")),
            init_point=data.get("init_point") or data.get("sandbox_init_point") or "",
            payload=data,
        )

    def get_preapproval(self, preapproval_id: str) -> dict[str, Any]:
        return self._request("GET", f"/preapproval/{preapproval_id}")

    def cancel_preapproval(self, preapproval_id: str) -> dict[str, Any]:
        return self._request("PUT", f"/preapproval/{preapproval_id}", json={"status": "cancelled"})

    def get_authorized_payment(self, authorized_payment_id: str) -> dict[str, Any]:
        """Cobrança de um ciclo da assinatura recorrente."""
        return self._request("GET", f"/authorized_payments/{authorized_payment_id}")

    # ------------------------------------------------------------------
    # Transporte
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise MercadoPagoNotConfigured()

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        if idempotent:
            headers["X-Idempotency-Key"] = str(uuid.uuid4())

        try:
            response = requests.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=headers,
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            # `error` pode conter a URL, nunca o header de autorização.
            logger.error("Mercado Pago indisponível em %s %s: %s", method, path, error)
            raise MercadoPagoError() from error

        logger.info("Mercado Pago %s %s -> %s", method, path, response.status_code)

        if response.status_code >= 400:
            detail = _safe_json(response)
            logger.error(
                "Mercado Pago recusou %s %s (%s): %s",
                method,
                path,
                response.status_code,
                mask_sensitive(detail),
            )
            raise MercadoPagoError(_friendly_error(detail))

        return _safe_json(response)


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


def _friendly_error(detail: dict[str, Any]) -> str:
    """Mensagem para o usuário final — sem stack trace nem jargão do provedor."""
    message = str(detail.get("message") or "").strip()
    if not message:
        return ""
    return f"O provedor de pagamento recusou a operação: {message}"


def _iso_expiration(minutes: int) -> str:
    from django.utils import timezone

    expires = timezone.now() + timezone.timedelta(minutes=minutes)
    # O Mercado Pago exige offset explícito no formato ISO-8601.
    return expires.astimezone().isoformat(timespec="milliseconds")


def get_client() -> MercadoPagoClient:
    """Ponto único de construção — facilita injetar um duplo nos testes."""
    return MercadoPagoClient()
