"""Regras de negócio de agendamento, atendimento e cancelamento.

Todas as operações que tocam mais de uma tabela rodam dentro de
`transaction.atomic()`. A criação e o reagendamento usam `select_for_update()`
sobre a agenda do barbeiro no dia, e a constraint única condicional do banco é
a última linha de defesa contra race condition.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls, time, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.appointments.models import (
    ALLOWED_TRANSITIONS,
    Appointment,
    AppointmentStatus,
    AppointmentStatusHistory,
    CancelledBy,
)
from apps.appointments.services.availability import build_availability, slot_end_time
from apps.core.exceptions import BusinessError, ConflictError

logger = logging.getLogger("suabarbearia.application")


# ---------------------------------------------------------------------------
# Criação
# ---------------------------------------------------------------------------
@transaction.atomic
def create_appointment(
    *,
    client,
    branch_id: int,
    barber_id: int,
    service_id: int,
    day: date_cls,
    start_time: time,
    notes: str = "",
    created_by=None,
    auto_confirm: bool = False,
) -> Appointment:
    """Cria um agendamento validando todas as regras de disponibilidade."""
    availability = build_availability(branch_id, barber_id, service_id, day)
    availability.validate_context()

    # Trava a agenda do barbeiro no dia para evitar reserva concorrente.
    _lock_barber_day(barber_id, day)

    if not availability.is_slot_available(start_time):
        raise ConflictError(
            "Este horário não está mais disponível. Escolha outro horário.",
            code="SLOT_NOT_AVAILABLE",
        )

    duration = availability.duration_minutes()
    end_time = slot_end_time(start_time, duration)
    price = availability.price()

    status = AppointmentStatus.CONFIRMED if auto_confirm else AppointmentStatus.PENDING

    try:
        appointment = Appointment.objects.create(
            client=client,
            barber=availability.barber,
            branch=availability.branch,
            service=availability.service,
            date=day,
            start_time=start_time,
            end_time=end_time,
            price=price,
            status=status,
            notes=notes,
            created_by=created_by,
            confirmed_at=timezone.now() if auto_confirm else None,
        )
    except IntegrityError as exc:
        logger.warning("Colisao de slot ao criar agendamento: %s", exc)
        raise ConflictError(
            "Este horário acabou de ser reservado por outro cliente.",
            code="SLOT_ALREADY_TAKEN",
        ) from exc

    _record_history(appointment, "", status, created_by, "Agendamento criado")
    _notify(appointment, "APPOINTMENT_CREATED")
    return appointment


# ---------------------------------------------------------------------------
# Reagendamento
# ---------------------------------------------------------------------------
@transaction.atomic
def reschedule_appointment(
    *,
    appointment: Appointment,
    day: date_cls,
    start_time: time,
    user,
    barber_id: int | None = None,
    reason: str = "",
) -> Appointment:
    """Move o agendamento para uma nova data/horário (e opcionalmente barbeiro)."""
    if appointment.is_final:
        raise BusinessError(
            "Este agendamento já foi finalizado e não pode ser remarcado.",
            code="APPOINTMENT_ALREADY_FINISHED",
        )
    if appointment.status == AppointmentStatus.IN_PROGRESS:
        raise BusinessError(
            "Não é possível remarcar um atendimento em andamento.",
            code="APPOINTMENT_IN_PROGRESS",
        )

    target_barber_id = barber_id or appointment.barber_id
    availability = build_availability(
        appointment.branch_id, target_barber_id, appointment.service_id, day
    )
    availability.validate_context()

    _lock_barber_day(target_barber_id, day)

    if not availability.is_slot_available(start_time, exclude_appointment_id=appointment.pk):
        raise ConflictError(
            "Este horário não está disponível. Escolha outro horário.",
            code="SLOT_NOT_AVAILABLE",
        )

    previous = {
        "date": appointment.date.isoformat(),
        "start_time": appointment.start_time.strftime("%H:%M"),
        "barber_id": appointment.barber_id,
    }

    duration = availability.duration_minutes()
    appointment.date = day
    appointment.start_time = start_time
    appointment.end_time = slot_end_time(start_time, duration)
    appointment.barber = availability.barber
    appointment.price = availability.price()

    try:
        appointment.save(
            update_fields=["date", "start_time", "end_time", "barber", "price", "updated_at"]
        )
    except IntegrityError as exc:
        raise ConflictError(
            "Este horário acabou de ser reservado por outro cliente.",
            code="SLOT_ALREADY_TAKEN",
        ) from exc

    AppointmentStatusHistory.objects.create(
        appointment=appointment,
        from_status=appointment.status,
        to_status=appointment.status,
        changed_by=user,
        reason=reason or "Reagendamento",
        metadata={
            "previous": previous,
            "new": {
                "date": day.isoformat(),
                "start_time": start_time.strftime("%H:%M"),
                "barber_id": appointment.barber_id,
            },
        },
    )
    _notify(appointment, "APPOINTMENT_RESCHEDULED")
    return appointment


# ---------------------------------------------------------------------------
# Cancelamento
# ---------------------------------------------------------------------------
@transaction.atomic
def cancel_appointment(*, appointment: Appointment, user, reason: str = "") -> Appointment:
    """Cancela respeitando o limite de antecedência configurado na filial."""
    if appointment.is_final:
        raise BusinessError(
            "Este agendamento já foi finalizado.", code="APPOINTMENT_ALREADY_FINISHED"
        )

    role = _resolve_cancel_role(appointment, user)

    if role == CancelledBy.CLIENT:
        limit_hours = appointment.branch.cancellation_limit_hours
        deadline = appointment.start_datetime - timedelta(hours=limit_hours)
        if timezone.now() > deadline:
            raise BusinessError(
                f"O cancelamento pelo aplicativo é permitido até {limit_hours} hora(s) "
                "antes do horário. Entre em contato com a barbearia.",
                code="CANCELLATION_DEADLINE_PASSED",
            )

    previous_status = appointment.status
    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancelled_at = timezone.now()
    appointment.cancelled_by = user if getattr(user, "is_authenticated", False) else None
    appointment.cancelled_by_role = role
    appointment.cancellation_reason = reason
    appointment.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancelled_by_role",
            "cancellation_reason",
            "updated_at",
        ]
    )

    from apps.finance.services import cancel_appointment_commission

    cancel_appointment_commission(appointment)

    _record_history(
        appointment, previous_status, AppointmentStatus.CANCELLED, user, reason or "Cancelado"
    )
    _notify(appointment, "APPOINTMENT_CANCELLED")
    return appointment


# ---------------------------------------------------------------------------
# Exclusão
# ---------------------------------------------------------------------------
@transaction.atomic
def delete_appointment(*, appointment: Appointment, user) -> None:
    """Apaga o agendamento do banco.

    Cancelar preserva o histórico; excluir some com o registro. As duas coisas
    fazem sentido — um horário lançado errado no balcão não deveria ficar para
    sempre na agenda —, mas excluir só é aceito enquanto nada de dinheiro foi
    gerado.

    Um atendimento concluído tem pagamento, receita no caixa, comissão do
    barbeiro e pontos de fidelidade pendurados nele. Apagá-lo faria os
    relatórios do mês deixarem de fechar. O `Payment` inclusive é `PROTECT`:
    sem esta checagem o banco recusaria com um erro incompreensível.
    """
    role = _resolve_delete_role(appointment, user)

    if role == CancelledBy.CLIENT:
        # Pelo aplicativo o cliente só desfaz um horário que ainda não começou,
        # e dentro do mesmo prazo do cancelamento. Sem isso, excluir viraria a
        # porta dos fundos: sumir com o horário 10 minutos antes deixaria a
        # barbearia sem registro nem da desistência nem da falta.
        if appointment.status not in (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED):
            raise BusinessError(
                "Este agendamento não pode mais ser excluído pelo aplicativo. "
                "Entre em contato com a barbearia.",
                code="APPOINTMENT_NOT_DELETABLE_BY_CLIENT",
            )

        limit_hours = appointment.branch.cancellation_limit_hours
        deadline = appointment.start_datetime - timedelta(hours=limit_hours)
        if timezone.now() > deadline:
            raise BusinessError(
                f"A exclusão pelo aplicativo é permitida até {limit_hours} hora(s) "
                "antes do horário. Entre em contato com a barbearia.",
                code="CANCELLATION_DEADLINE_PASSED",
            )

    if appointment.status == AppointmentStatus.COMPLETED:
        raise BusinessError(
            "Um atendimento concluído não pode ser excluído: ele já gerou pagamento, "
            "comissão e pontos de fidelidade. Ele faz parte do histórico financeiro.",
            code="APPOINTMENT_COMPLETED_CANNOT_DELETE",
        )

    if appointment.payments.exists():
        raise BusinessError(
            "Este agendamento tem pagamento registrado e não pode ser excluído. "
            "Estorne o pagamento antes.",
            code="APPOINTMENT_HAS_PAYMENT",
        )

    logger.info(
        "Agendamento %s (%s, cliente %s, %s %s) excluido por %s",
        appointment.pk,
        appointment.status,
        appointment.client_id,
        appointment.date,
        appointment.start_time,
        getattr(user, "pk", None),
    )
    appointment.delete()


# ---------------------------------------------------------------------------
# Transições de status
# ---------------------------------------------------------------------------
@transaction.atomic
def transition_status(
    *, appointment: Appointment, to_status: str, user, reason: str = ""
) -> Appointment:
    """Aplica uma transição de status validada pela máquina de estados."""
    if to_status not in ALLOWED_TRANSITIONS.get(appointment.status, ()):
        raise BusinessError(
            f"Não é possível mudar de '{appointment.get_status_display()}' para "
            f"'{AppointmentStatus(to_status).label}'.",
            code="INVALID_STATUS_TRANSITION",
        )

    if to_status == AppointmentStatus.CANCELLED:
        return cancel_appointment(appointment=appointment, user=user, reason=reason)

    previous_status = appointment.status
    now = timezone.now()
    appointment.status = to_status
    fields = ["status", "updated_at"]

    timestamps = {
        AppointmentStatus.CONFIRMED: "confirmed_at",
        AppointmentStatus.ARRIVED: "arrived_at",
        AppointmentStatus.IN_PROGRESS: "started_at",
        AppointmentStatus.COMPLETED: "completed_at",
    }
    field = timestamps.get(to_status)
    if field:
        setattr(appointment, field, now)
        fields.append(field)

    appointment.save(update_fields=fields)
    _record_history(appointment, previous_status, to_status, user, reason)

    if to_status == AppointmentStatus.CONFIRMED:
        _notify(appointment, "APPOINTMENT_CONFIRMED")
    return appointment


# ---------------------------------------------------------------------------
# Conclusão do atendimento
# ---------------------------------------------------------------------------
@transaction.atomic
def complete_appointment(
    *,
    appointment: Appointment,
    user,
    payment_method: str,
    amount: Decimal | None = None,
    discount_amount: Decimal = Decimal("0.00"),
    notes: str = "",
) -> Appointment:
    """Finaliza o atendimento gerando pagamento, caixa, comissão e pontos.

    Sequência (uma única transação):
      1. Marca o agendamento como COMPLETED.
      2. Aplica o benefício do plano mensal, se o cliente tiver um.
      3. Cria o `Payment` já quitado.
      4. Lança a receita no financeiro.
      5. Gera a comissão do barbeiro.
      6. Credita pontos de fidelidade ao cliente.
      7. Atualiza os agregados do cliente.
    """
    from apps.finance.services import create_appointment_commission, record_income
    from apps.loyalty.services import earn_points
    from apps.payments.models import Payment, PaymentMethod, PaymentStatus
    from apps.plans.services import consume_quota, coverage_for

    if appointment.status not in (
        AppointmentStatus.IN_PROGRESS,
        AppointmentStatus.ARRIVED,
        AppointmentStatus.CONFIRMED,
    ):
        raise BusinessError(
            "Só é possível finalizar um atendimento iniciado.",
            code="APPOINTMENT_NOT_STARTED",
        )

    gross = Decimal(amount) if amount is not None else appointment.price
    discount = Decimal(discount_amount or 0)
    if discount > gross:
        raise BusinessError(
            "O desconto não pode ser maior que o valor do atendimento.",
            code="DISCOUNT_GREATER_THAN_AMOUNT",
        )

    # O plano mensal só entra quando ninguém informou desconto à mão: um
    # desconto explícito do balcão é uma decisão do operador e tem prioridade.
    coverage = coverage_for(
        appointment.client, appointment.service, branch_id=appointment.branch_id
    )
    # `plan_covered` é o que decide consumir a cota. Sem essa distinção, um
    # desconto manual gastaria um uso do plano *e* cobraria o cliente.
    plan_covered = False
    if discount == 0 and coverage.has_benefit:
        if coverage.is_covered:
            discount = gross
            payment_method = PaymentMethod.SUBSCRIPTION
            plan_covered = True
        else:
            discount = (gross * coverage.discount_percentage / Decimal("100")).quantize(
                Decimal("0.01")
            )

    net = gross - discount
    # O barbeiro trabalhou mesmo sem entrada no caixa: a comissão de um
    # atendimento coberto incide sobre o preço do serviço, se o plano permitir.
    commission_base = net
    if plan_covered and coverage.subscription.plan.pays_barber_commission:
        commission_base = gross

    previous_status = appointment.status
    now = timezone.now()
    appointment.status = AppointmentStatus.COMPLETED
    appointment.completed_at = now
    appointment.price = gross
    if notes:
        appointment.internal_notes = (
            f"{appointment.internal_notes}\n{notes}".strip()
            if appointment.internal_notes
            else notes
        )
    if appointment.started_at is None:
        appointment.started_at = now
    appointment.save(
        update_fields=[
            "status",
            "completed_at",
            "started_at",
            "price",
            "internal_notes",
            "updated_at",
        ]
    )

    payment = Payment.objects.create(
        branch=appointment.branch,
        appointment=appointment,
        client=appointment.client,
        amount=gross,
        discount_amount=discount,
        method=payment_method,
        status=PaymentStatus.PAID,
        paid_at=now,
        notes=notes,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )

    # Atendimento 100% coberto não gera receita aqui: o dinheiro entrou na
    # fatura da assinatura. Lançar zero só poluiria o caixa.
    if net > 0:
        record_income(
            branch=appointment.branch,
            amount=net,
            description=f"{appointment.service.name} - {appointment.client.full_name}",
            payment=payment,
            appointment=appointment,
            barber=appointment.barber,
            date=appointment.date,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )

    if plan_covered:
        consume_quota(coverage=coverage, appointment=appointment, amount=gross)

    create_appointment_commission(appointment, commission_base)

    earn_points(
        client=appointment.client,
        branch=appointment.branch,
        amount=net,
        description=f"Atendimento: {appointment.service.name}",
        appointment=appointment,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )

    _update_client_aggregates(appointment, net)
    _record_history(
        appointment, previous_status, AppointmentStatus.COMPLETED, user, "Atendimento concluído"
    )
    _notify(appointment, "APPOINTMENT_COMPLETED")
    return appointment


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------
def _lock_barber_day(barber_id: int, day: date_cls) -> None:
    """Bloqueia as linhas da agenda do barbeiro no dia (serializa concorrentes)."""
    list(
        Appointment.objects.select_for_update()
        .filter(barber_id=barber_id, date=day)
        .values_list("pk", flat=True)
    )


def _resolve_delete_role(appointment: Appointment, user) -> str:
    """Quem pode excluir: o dono, ou o cliente sobre o próprio horário.

    O barbeiro fica de fora de propósito: apagar o próprio atendimento faria
    sumir a evidência de uma falta ou de um cancelamento em cima da hora.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        raise BusinessError("Autenticação necessária.", code="PERMISSION_DENIED", status_code=403)
    if user.is_superuser or user.is_owner:
        return CancelledBy.OWNER

    client = getattr(user, "client_profile", None)
    if client is not None and client.pk == appointment.client_id:
        return CancelledBy.CLIENT

    raise BusinessError(
        "Você não pode excluir este agendamento. Use o cancelamento.",
        code="PERMISSION_DENIED",
        status_code=403,
    )


def _resolve_cancel_role(appointment: Appointment, user) -> str:
    if user is None or not getattr(user, "is_authenticated", False):
        return CancelledBy.SYSTEM
    if user.is_superuser or user.is_owner:
        return CancelledBy.OWNER
    if user.is_barber:
        barber = getattr(user, "barber_profile", None)
        if barber is None or barber.pk != appointment.barber_id:
            raise BusinessError(
                "Você só pode cancelar os seus próprios atendimentos.",
                code="PERMISSION_DENIED",
                status_code=403,
            )
        return CancelledBy.BARBER
    client = getattr(user, "client_profile", None)
    if client is None or client.pk != appointment.client_id:
        raise BusinessError(
            "Você só pode cancelar os seus próprios agendamentos.",
            code="PERMISSION_DENIED",
            status_code=403,
        )
    return CancelledBy.CLIENT


def _record_history(
    appointment: Appointment, from_status: str, to_status: str, user, reason: str
) -> None:
    AppointmentStatusHistory.objects.create(
        appointment=appointment,
        from_status=from_status or "",
        to_status=to_status,
        changed_by=user if getattr(user, "is_authenticated", False) else None,
        reason=reason,
    )


def _update_client_aggregates(appointment: Appointment, net_amount: Decimal) -> None:
    """Atualiza visitas, total gasto, última visita e serviço favorito."""
    from django.db.models import Count, F

    from apps.clients.models import Client

    client = appointment.client
    favorite = (
        Appointment.objects.filter(client=client, status=AppointmentStatus.COMPLETED)
        .values("service")
        .annotate(total=Count("service"))
        .order_by("-total")
        .first()
    )

    # F() evita perder incrementos concorrentes e não depende do valor em memória.
    Client.objects.filter(pk=client.pk).update(
        total_visits=F("total_visits") + 1,
        total_spent=F("total_spent") + net_amount,
        last_visit_at=appointment.completed_at or timezone.now(),
        favorite_service_id=favorite["service"] if favorite else None,
    )


def _notify(appointment: Appointment, notification_type: str) -> None:
    """Dispara a notificação de forma assíncrona, sem bloquear o request."""
    from apps.notifications.tasks import notify_appointment_event

    transaction.on_commit(lambda: notify_appointment_event.delay(appointment.pk, notification_type))
