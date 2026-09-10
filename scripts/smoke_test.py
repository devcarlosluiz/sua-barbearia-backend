"""Teste de fumaça da API Sua Barbearia (usa apenas a biblioteca padrão).

Executa o fluxo real ponta a ponta contra uma instância em execução:
login → filiais → serviços → barbeiros → horários livres → agendamento →
atendimento (chegada, início, finalização com pagamento) → avaliação.

Uso:
    python scripts/smoke_test.py [http://localhost:8000]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
PASSWORD = "SuaBarbearia@2026"

OK = "\033[92mOK\033[0m"
FAIL = "\033[91mFALHOU\033[0m"


class ApiError(Exception):
    pass


def request(
    method: str, path: str, token: str | None = None, payload: dict[str, Any] | None = None
) -> Any:
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        raise ApiError(f"{method} {path} -> {exc.code}: {body[:400]}") from exc

    if not body:
        return None
    parsed = json.loads(body)
    if isinstance(parsed, dict) and "data" in parsed:
        return parsed["data"]
    return parsed


def login(email: str) -> str:
    data = request("POST", "/api/v1/auth/login/", payload={"email": email, "password": PASSWORD})
    return data["access"]


def next_business_day(offset: int = 3) -> str:
    day = date.today() + timedelta(days=offset)
    while day.weekday() == 6:  # domingo: filiais fechadas
        day += timedelta(days=1)
    return day.isoformat()


def step(label: str, value: Any = "") -> None:
    print(f"  {OK}  {label}{f' -> {value}' if value != '' else ''}")


def main() -> int:
    print("\n=== Sua Barbearia :: teste de fumaça da API ===\n")

    health = request("GET", "/health/")
    assert health["status"] == "healthy", health
    step("health check", health["checks"])

    client_token = login("client@suabarbearia.com")
    owner_token = login("owner@suabarbearia.com")
    barber_token = login("barber@suabarbearia.com")
    step("login owner/barber/client")

    me = request("GET", "/api/v1/auth/me/", client_token)
    step("GET /auth/me", me["user"]["role"])

    branches = request("GET", "/api/v1/branches/", client_token)["results"]
    assert branches, "nenhuma filial cadastrada — rode o seed_data"
    branch = branches[0]
    step("filiais", f"{len(branches)} ({branch['name']})")

    barbers = request("GET", f"/api/v1/barbers/?branch={branch['id']}", client_token)["results"]
    assert barbers, "nenhum barbeiro na filial"
    barber = barbers[0]
    step("barbeiros da filial", f"{len(barbers)} ({barber['name']})")

    barber_services = request("GET", f"/api/v1/barbers/{barber['id']}/services/", client_token)
    assert barber_services, "barbeiro sem serviços"
    service = barber_services[0]
    step("serviços do barbeiro", service["service_detail"]["name"])

    day = next_business_day()
    slots_response = request(
        "GET",
        f"/api/v1/appointments/available-slots/?branch_id={branch['id']}"
        f"&barber_id={barber['id']}&service_id={service['service']}&date={day}",
        client_token,
    )
    slots = slots_response["slots"]
    assert slots, f"nenhum horário livre em {day}"
    step("horários livres", f"{len(slots)} em {day} (1º: {slots[0]})")

    appointment = request(
        "POST",
        "/api/v1/appointments/",
        client_token,
        {
            "branch_id": branch["id"],
            "barber_id": barber["id"],
            "service_id": service["service"],
            "date": day,
            "start_time": slots[0],
            "notes": "Agendamento criado pelo teste de fumaça.",
        },
    )
    step("agendamento criado", f"#{appointment['id']} {appointment['status']}")

    # O slot recém-ocupado não pode mais aparecer como disponível.
    slots_after = request(
        "GET",
        f"/api/v1/appointments/available-slots/?branch_id={branch['id']}"
        f"&barber_id={barber['id']}&service_id={service['service']}&date={day}",
        client_token,
    )["slots"]
    assert slots[0] not in slots_after, "o horário reservado continua sendo oferecido!"
    step("slot removido da disponibilidade", slots[0])

    # Reserva duplicada deve ser rejeitada com 409.
    try:
        request(
            "POST",
            "/api/v1/appointments/",
            client_token,
            {
                "branch_id": branch["id"],
                "barber_id": barber["id"],
                "service_id": service["service"],
                "date": day,
                "start_time": slots[0],
            },
        )
        print(f"  {FAIL}  reserva duplicada foi aceita")
        return 1
    except ApiError as exc:
        assert "SLOT" in str(exc), exc
        step("reserva duplicada bloqueada", "409 SLOT_NOT_AVAILABLE")

    # Cliente não pode acessar o financeiro.
    try:
        request("GET", "/api/v1/finance/transactions/", client_token)
        print(f"  {FAIL}  cliente conseguiu acessar o financeiro")
        return 1
    except ApiError as exc:
        assert "403" in str(exc), exc
        step("cliente bloqueado no financeiro", "403")

    # Fluxo de atendimento pelo OWNER (o barbeiro do slot pode não ser o barber@).
    request(
        "POST",
        f"/api/v1/appointments/{appointment['id']}/status/",
        owner_token,
        {"status": "CONFIRMED"},
    )
    request("POST", f"/api/v1/appointments/{appointment['id']}/arrive/", owner_token)
    request("POST", f"/api/v1/appointments/{appointment['id']}/start/", owner_token)
    completed = request(
        "POST",
        f"/api/v1/appointments/{appointment['id']}/complete/",
        owner_token,
        {"payment_method": "PIX", "notes": "Atendimento do teste de fumaça."},
    )
    assert completed["status"] == "COMPLETED", completed
    step("atendimento concluído", f"R$ {completed['price']}")

    review = request(
        "POST",
        "/api/v1/reviews/",
        client_token,
        {"appointment": appointment["id"], "rating": 5, "comment": "Excelente atendimento!"},
    )
    step("avaliação registrada", f"{review['rating']}/5")

    try:
        request(
            "POST",
            "/api/v1/reviews/",
            client_token,
            {"appointment": appointment["id"], "rating": 4},
        )
        print(f"  {FAIL}  avaliação duplicada foi aceita")
        return 1
    except ApiError as exc:
        assert "REVIEW_ALREADY_EXISTS" in str(exc), exc
        step("avaliação duplicada bloqueada")

    owner_dash = request("GET", "/api/v1/dashboard/owner/?period=30d", owner_token)
    step("dashboard owner", f"faturamento período R$ {owner_dash['kpis']['revenue_period']}")

    barber_dash = request("GET", "/api/v1/dashboard/barber/", barber_token)
    step("dashboard barbeiro", f"{barber_dash['kpis']['appointments_today']} atend. hoje")

    client_dash = request("GET", "/api/v1/dashboard/client/", client_token)
    step("dashboard cliente", f"{client_dash['loyalty_points']} pontos")

    loyalty = request("GET", "/api/v1/loyalty/accounts/me/", client_token)
    step("fidelidade", f"saldo {loyalty['account']['balance']} pts")

    print(f"\n{OK} — fluxo completo validado com dados reais.\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ApiError, AssertionError) as error:
        print(f"\n{FAIL}: {error}\n")
        sys.exit(1)
