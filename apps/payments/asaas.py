"""Cliente HTTP do Asaas.

Cobre só o que a Sua Barbearia usa:

* **PIX avulso** (`/v3/payments` + `/v3/payments/{id}/pixQrCode`) — a cobrança
  de cada ciclo no plano PIX.
* **Assinatura recorrente** (`/v3/subscriptions`) — o cartão de crédito. O
  cliente informa o cartão na fatura hospedada do Asaas (`invoiceUrl`); nem o
  app nem este backend recebem número, CVV ou validade. Isso mantém os dados
  de cartão fora do nosso escopo de PCI.
* **Consulta** de pagamento, usada pelo webhook e pela varredura de segurança
  que roda no Celery.

A `access_token` (chave de API) nunca é registrada em log: `_request` loga
método, caminho e status, e o corpo da resposta só entra em log de erro depois
de passar por `mask_sensitive`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from apps.core.exceptions import BusinessError

logger = logging.getLogger("suabarbearia.application")

DEFAULT_TIMEOUT = 20

#: Chaves que nunca devem aparecer em log, mesmo vindas do provedor.
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "creditcard",
        "creditcardtoken",
        "creditcardnumber",
        "creditcardholderinfo",
        "ccv",
        "cpfcnpj",
        "authtoken",
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


class AsaasNotConfigured(BusinessError):
    """Falta a chave de API: a assinatura online não pode ser oferecida."""

    def __init__(self) -> None:
        super().__init__(
            "O pagamento online não está configurado. Fale com a barbearia.",
            code="GATEWAY_NOT_CONFIGURED",
            status_code=503,
        )


class AsaasError(BusinessError):
    """Erro devolvido pelo provedor, já traduzido para o cliente."""

    def __init__(self, message: str = "", *, code: str = "GATEWAY_ERROR") -> None:
        super().__init__(
            message or "Não foi possível falar com o Asaas. Tente novamente.",
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
class CardSubscription:
    """Assinatura recorrente criada no provedor, com o 1º ciclo já emitido."""

    external_id: str
    status: str
    checkout_url: str
    #: Id do pagamento do 1º ciclo — é o que o webhook usa para achar a fatura.
    first_payment_id: str
    payload: dict[str, Any]


class AsaasClient:
    """Wrapper fino sobre a API REST do Asaas."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key or settings.ASAAS_API_KEY
        self._base_url = (base_url or settings.ASAAS_BASE_URL).rstrip("/")
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------------
    # Cliente (o Asaas exige um `customer` antes de qualquer cobrança)
    # ------------------------------------------------------------------
    def get_or_create_customer(
        self,
        *,
        external_reference: str,
        name: str,
        email: str,
        cpf: str = "",
    ) -> str:
        existing = self._request(
            "GET", "/customers", params={"externalReference": external_reference}
        )
        for row in existing.get("data") or []:
            customer_id = row.get("id")
            if customer_id:
                return str(customer_id)

        payload: dict[str, Any] = {
            "name": name or "Cliente Sua Barbearia",
            "email": email,
            "externalReference": external_reference,
        }
        if cpf:
            payload["cpfCnpj"] = cpf

        data = self._request("POST", "/customers", json=payload)
        return str(data.get("id", ""))

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
        payer_name: str = "",
        payer_cpf: str = "",
        expires_in_minutes: int = 60,
    ) -> PixCharge:
        customer_id = self.get_or_create_customer(
            external_reference=external_reference,
            name=payer_name,
            email=payer_email,
            cpf=payer_cpf,
        )

        due_date = (timezone.now() + timedelta(minutes=expires_in_minutes)).date()
        payload: dict[str, Any] = {
            "customer": customer_id,
            "billingType": "PIX",
            "value": float(Decimal(amount)),
            "dueDate": due_date.isoformat(),
            "description": description,
            "externalReference": external_reference,
        }

        data = self._request("POST", "/payments", json=payload)
        payment_id = str(data.get("id", ""))
        qr_code = self._request("GET", f"/payments/{payment_id}/pixQrCode")

        return PixCharge(
            external_id=payment_id,
            status=str(data.get("status", "")),
            qr_code=qr_code.get("payload", "") or "",
            qr_code_base64=qr_code.get("encodedImage", "") or "",
            ticket_url=data.get("invoiceUrl") or "",
            expires_at=qr_code.get("expirationDate"),
            payload=data,
        )

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/payments/{payment_id}")

    def refund_payment(self, payment_id: str, amount: Decimal | None = None) -> dict[str, Any]:
        body = {"value": float(Decimal(amount))} if amount is not None else {}
        return self._request("POST", f"/payments/{payment_id}/refund", json=body)

    # ------------------------------------------------------------------
    # Assinatura recorrente no cartão
    # ------------------------------------------------------------------
    def create_card_subscription(
        self,
        *,
        amount: Decimal,
        reason: str,
        external_reference: str,
        payer_email: str,
        payer_name: str = "",
        payer_cpf: str = "",
    ) -> CardSubscription:
        """Cria a assinatura recorrente.

        `billingType="UNDEFINED"` deixa o cliente escolher a forma de
        pagamento (incluindo cartão) na fatura hospedada do Asaas
        (`invoiceUrl`) — o mesmo papel do `init_point` do provedor anterior.
        A confirmação de cada ciclo chega por webhook.
        """
        customer_id = self.get_or_create_customer(
            external_reference=external_reference,
            name=payer_name,
            email=payer_email,
            cpf=payer_cpf,
        )

        payload: dict[str, Any] = {
            "customer": customer_id,
            "billingType": "UNDEFINED",
            "cycle": "MONTHLY",
            "value": float(Decimal(amount)),
            "nextDueDate": timezone.localdate().isoformat(),
            "description": reason,
            "externalReference": external_reference,
        }

        data = self._request("POST", "/subscriptions", json=payload)
        subscription_id = str(data.get("id", ""))
        first_payment = self._first_payment_of(subscription_id) or {}

        return CardSubscription(
            external_id=subscription_id,
            status=str(data.get("status", "")),
            checkout_url=first_payment.get("invoiceUrl") or "",
            first_payment_id=str(first_payment.get("id") or ""),
            payload=data,
        )

    def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        return self._request("GET", f"/subscriptions/{subscription_id}")

    def cancel_subscription(self, subscription_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/subscriptions/{subscription_id}")

    def _first_payment_of(self, subscription_id: str) -> dict[str, Any] | None:
        data = self._request(
            "GET", "/payments", params={"subscription": subscription_id, "limit": 1}
        )
        rows = data.get("data") or []
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Transporte
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise AsaasNotConfigured()

        headers = {
            "access_token": self._api_key,
            "Content-Type": "application/json",
        }

        try:
            response = requests.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            # `error` pode conter a URL, nunca o header de autenticação.
            logger.error("Asaas indisponível em %s %s: %s", method, path, error)
            raise AsaasError() from error

        logger.info("Asaas %s %s -> %s", method, path, response.status_code)

        if response.status_code >= 400:
            detail = _safe_json(response)
            logger.error(
                "Asaas recusou %s %s (%s): %s",
                method,
                path,
                response.status_code,
                mask_sensitive(detail),
            )
            raise AsaasError(_friendly_error(detail))

        return _safe_json(response)


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


def _friendly_error(detail: dict[str, Any]) -> str:
    """Mensagem para o usuário final — sem stack trace nem jargão do provedor."""
    errors = detail.get("errors")
    if isinstance(errors, list) and errors:
        message = str(errors[0].get("description") or "").strip()
        if message:
            return f"O provedor de pagamento recusou a operação: {message}"
    return ""


def get_client() -> AsaasClient:
    """Ponto único de construção — facilita injetar um duplo nos testes."""
    return AsaasClient()
