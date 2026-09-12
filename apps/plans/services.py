"""Regras de assinatura: ciclo, cobrança, ativação e consumo de cota.

As views não falam com o gateway nem calculam datas: tudo passa por aqui, o
que mantém uma única definição de "ciclo" e de "assinatura ativa".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_cls
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.core.exceptions import BusinessError, ConflictError
from apps.payments import mercadopago
from apps.payments.models import PaymentMethod, PaymentProvider
from apps.plans.models import (
    LIVE_SUBSCRIPTION_STATUSES,
    BillingType,
    InvoiceStatus,
    Plan,
    PlanService,
    Subscription,
    SubscriptionInvoice,
    SubscriptionStatus,
    SubscriptionUsage,
)

logger = logging.getLogger("suabarbearia.application")


def _config(key: str) -> int:
    return int(settings.SUBSCRIPTION_SETTINGS[key])


# ---------------------------------------------------------------------------
# Ciclo
# ---------------------------------------------------------------------------
def period_bounds(start: date_cls) -> tuple[date_cls, date_cls]:
    """Ciclo mensal fechado: começa em `start` e termina no dia anterior ao
    mesmo dia do mês seguinte.

    Usa `relativedelta` para não errar em 31/01 -> 28/02.
    """
    return start, start + relativedelta(months=1) - relativedelta(days=1)


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
def live_subscription_for(client) -> Subscription | None:
    """Assinatura viva do cliente (pendente, ativa ou atrasada)."""
    return (
        Subscription.objects.select_related("plan", "client__user", "branch")
        .filter(client=client, status__in=("PENDING_PAYMENT", "ACTIVE", "PAST_DUE"))
        .first()
    )


def active_subscription_for(client) -> Subscription | None:
    """Assinatura que efetivamente concede benefício agora."""
    subscription = live_subscription_for(client)
    if subscription is None or not subscription.grants_benefit:
        return None
    return subscription


@dataclass
class Coverage:
    """Como o plano do cliente afeta o preço de um serviço."""

    subscription: Subscription | None = None
    plan_service: PlanService | None = None
    is_covered: bool = False
    discount_percentage: Decimal = Decimal("0.00")
    remaining: int | None = None

    @property
    def has_benefit(self) -> bool:
        return self.is_covered or self.discount_percentage > 0


def coverage_for(client, service, branch_id: int | None = None) -> Coverage:
    """Resolve o benefício do plano para um serviço.

    Três resultados possíveis:
      * cota disponível  -> `is_covered`, o cliente não paga nada;
      * cota esgotada    -> `discount_percentage` do plano, se houver;
      * fora do plano    -> nenhum benefício.
    """
    subscription = active_subscription_for(client)
    if subscription is None:
        return Coverage()
    if not subscription.plan.covers_branch(branch_id):
        return Coverage()

    plan_service = PlanService.objects.filter(plan=subscription.plan_id, service=service).first()
    if plan_service is None:
        return Coverage(subscription=subscription)

    remaining = remaining_quota(subscription, plan_service)
    if remaining is None or remaining > 0:
        return Coverage(
            subscription=subscription,
            plan_service=plan_service,
            is_covered=True,
            remaining=remaining,
        )

    return Coverage(
        subscription=subscription,
        plan_service=plan_service,
        discount_percentage=subscription.plan.overage_discount_percentage,
        remaining=0,
    )


def remaining_quota(subscription: Subscription, plan_service: PlanService) -> int | None:
    """Usos restantes no ciclo. `None` significa ilimitado."""
    if plan_service.is_unlimited:
        return None
    used = SubscriptionUsage.objects.filter(
        subscription=subscription,
        service=plan_service.service_id,
        period_start=subscription.current_period_start,
    ).count()
    return max(plan_service.monthly_quota - used, 0)


def quota_summary(subscription: Subscription) -> list[dict[str, object]]:
    """Cota por serviço no ciclo atual — é o que o cliente vê no app."""
    usage = dict(
        SubscriptionUsage.objects.filter(
            subscription=subscription, period_start=subscription.current_period_start
        )
        .values_list("service_id")
        .annotate(total=Count("id"))
    )
    summary = []
    for plan_service in subscription.plan.plan_services.select_related("service").all():
        used = usage.get(plan_service.service_id, 0)
        summary.append(
            {
                "service": plan_service.service_id,
                "service_name": plan_service.service.name,
                "monthly_quota": plan_service.monthly_quota,
                "is_unlimited": plan_service.is_unlimited,
                "used": used,
                "remaining": None
                if plan_service.is_unlimited
                else max(plan_service.monthly_quota - used, 0),
            }
        )
    return summary


# ---------------------------------------------------------------------------
# Cobertura exibida no atendimento
# ---------------------------------------------------------------------------
def appointment_coverage_map(appointments) -> dict[int, dict[str, object]]:
    """Como o plano do cliente afeta cada atendimento da lista.

    Separado de `coverage_for` por causa do custo: a agenda do dia tem dezenas
    de linhas e uma chamada por linha seriam três consultas por atendimento.
    Aqui a lista inteira sai em três, independentemente do tamanho.

    Devolve `None` para o atendimento sem nada a dizer — cliente sem plano, ou
    atendimento já encerrado que não consumiu cota.
    """
    appointments = [appointment for appointment in appointments if appointment is not None]
    if not appointments:
        return {}

    # Um atendimento concluído carrega a própria verdade: o uso gravado na
    # finalização. Recalcular a cota de hoje diria "coberto" sobre um corte que
    # o cliente pagou em dinheiro no ciclo passado.
    usages = {
        usage.appointment_id: usage
        for usage in SubscriptionUsage.objects.filter(
            appointment_id__in=[appointment.pk for appointment in appointments]
        ).select_related("subscription__plan")
    }

    pending = [
        appointment
        for appointment in appointments
        if appointment.pk not in usages and not appointment.is_final
    ]
    subscriptions: dict[int, Subscription] = {}
    if pending:
        subscriptions = {
            subscription.client_id: subscription
            for subscription in Subscription.objects.filter(
                client_id__in={appointment.client_id for appointment in pending},
                status__in=LIVE_SUBSCRIPTION_STATUSES,
            )
            .select_related("plan")
            .prefetch_related("plan__branches", "plan__plan_services")
        }

    used = _usage_counts(subscriptions.values())

    coverage_map: dict[int, dict[str, object] | None] = {}
    for appointment in appointments:
        usage = usages.get(appointment.pk)
        if usage is not None:
            coverage_map[appointment.pk] = _consumed_payload(usage)
        elif appointment.is_final:
            coverage_map[appointment.pk] = None
        else:
            coverage_map[appointment.pk] = _pending_payload(
                appointment, subscriptions.get(appointment.client_id), used
            )
    return coverage_map


def _usage_counts(subscriptions) -> dict[tuple[int, int, date_cls], int]:
    """Usos por (assinatura, serviço) no ciclo corrente de cada assinatura."""
    subscriptions = [
        subscription
        for subscription in subscriptions
        if subscription.current_period_start is not None
    ]
    if not subscriptions:
        return {}

    rows = (
        SubscriptionUsage.objects.filter(
            subscription_id__in=[subscription.pk for subscription in subscriptions],
            period_start__in={subscription.current_period_start for subscription in subscriptions},
        )
        .values("subscription_id", "service_id", "period_start")
        .annotate(total=Count("id"))
    )
    return {
        (row["subscription_id"], row["service_id"], row["period_start"]): row["total"]
        for row in rows
    }


def _consumed_payload(usage: SubscriptionUsage) -> dict[str, object]:
    """Atendimento que já consumiu uma cota — nada a cobrar, nunca."""
    plan = usage.subscription.plan
    return _payload(
        subscription=usage.subscription,
        covers_service=True,
        is_covered=True,
        remaining=None,
        discount_percentage=Decimal("0.00"),
        amount_due=Decimal("0.00"),
        label=f"Coberto pelo plano {plan.name}",
    )


def _pending_payload(appointment, subscription, used) -> dict[str, object] | None:
    """Benefício de um atendimento que ainda vai ser cobrado."""
    if subscription is None:
        return None

    plan = subscription.plan
    price = Decimal(appointment.price)
    full_price = {
        "subscription": subscription,
        "covers_service": False,
        "is_covered": False,
        "remaining": None,
        "discount_percentage": Decimal("0.00"),
        "amount_due": price,
    }

    if not subscription.grants_benefit:
        return _payload(
            **full_price,
            label=f"Plano {plan.name} sem benefício ativo ({subscription.get_status_display()})",
        )
    if not plan.covers_branch(appointment.branch_id):
        return _payload(**full_price, label=f"O plano {plan.name} não vale nesta filial")

    plan_service = next(
        (item for item in plan.plan_services.all() if item.service_id == appointment.service_id),
        None,
    )
    if plan_service is None:
        return _payload(**full_price, label=f"O plano {plan.name} não cobre este serviço")

    remaining = None
    if not plan_service.is_unlimited:
        consumed = used.get(
            (subscription.pk, plan_service.service_id, subscription.current_period_start), 0
        )
        remaining = max(plan_service.monthly_quota - consumed, 0)

    if remaining is None or remaining > 0:
        return _payload(
            subscription=subscription,
            covers_service=True,
            is_covered=True,
            remaining=remaining,
            discount_percentage=Decimal("0.00"),
            amount_due=Decimal("0.00"),
            label=f"Coberto pelo plano {plan.name}",
        )

    # Cota esgotada: o cliente paga, com o desconto do plano se houver.
    discount = plan.overage_discount_percentage
    amount_due = price - (price * discount / Decimal("100")).quantize(Decimal("0.01"))
    label = "Cota do plano esgotada neste ciclo"
    if discount > 0:
        label = f"{label} — {discount:.0f}% de desconto"
    return _payload(
        subscription=subscription,
        covers_service=True,
        is_covered=False,
        remaining=0,
        discount_percentage=discount,
        amount_due=amount_due,
        label=label,
    )


def _payload(
    *,
    subscription: Subscription,
    covers_service: bool,
    is_covered: bool,
    remaining: int | None,
    discount_percentage: Decimal,
    amount_due: Decimal,
    label: str,
) -> dict[str, object]:
    """Formato único do campo `plan_coverage` do agendamento.

    Os decimais saem como string pelo mesmo motivo que no resto da API: o app
    não deve arredondar dinheiro em ponto flutuante.
    """
    return {
        "is_subscriber": True,
        "plan_id": subscription.plan_id,
        "plan_name": subscription.plan.name,
        "subscription_status": subscription.status,
        "subscription_status_display": subscription.get_status_display(),
        "covers_service": covers_service,
        "is_covered": is_covered,
        "remaining": remaining,
        "discount_percentage": f"{Decimal(discount_percentage):.2f}",
        "amount_due": f"{Decimal(amount_due):.2f}",
        "charge_client": Decimal(amount_due) > 0,
        "label": label,
    }


# ---------------------------------------------------------------------------
# Assinar
# ---------------------------------------------------------------------------
@transaction.atomic
def subscribe(
    *,
    client,
    plan: Plan,
    billing_type: str,
    branch=None,
) -> Subscription:
    """Cria a assinatura e a cobrança do primeiro ciclo.

    A assinatura nasce em `PENDING_PAYMENT`: o benefício só vale quando o
    pagamento é confirmado (por webhook ou pelo caixa). Isso evita conceder
    cota por um PIX que nunca foi pago.
    """
    if not plan.is_active:
        raise BusinessError("Este plano não está mais disponível.", code="PLAN_UNAVAILABLE")
    if billing_type not in BillingType.values:
        raise BusinessError("Forma de cobrança inválida.", code="INVALID_BILLING_TYPE")
    if branch is not None and not plan.covers_branch(branch.id):
        raise BusinessError(
            "Este plano não é válido na filial escolhida.", code="PLAN_BRANCH_NOT_ALLOWED"
        )

    existing = live_subscription_for(client)
    if existing is not None:
        raise ConflictError(
            "Você já tem uma assinatura em andamento. Cancele a atual para assinar outra.",
            code="SUBSCRIPTION_ALREADY_EXISTS",
        )

    subscription = Subscription.objects.create(
        client=client,
        plan=plan,
        branch=branch,
        billing_type=billing_type,
        price=plan.price,
        status=SubscriptionStatus.PENDING_PAYMENT,
        provider=PaymentProvider.MERCADO_PAGO,
    )

    if billing_type == BillingType.CARD_RECURRING:
        _start_card_recurring(subscription)
    else:
        issue_pix_invoice(subscription, period_start=timezone.localdate())

    return subscription


def _start_card_recurring(subscription: Subscription) -> None:
    """Cria a assinatura recorrente e guarda o `init_point` do checkout."""
    client = mercadopago.get_client()
    preapproval = client.create_preapproval(
        amount=subscription.price,
        reason=f"Sua Barbearia - {subscription.plan.name}",
        external_reference=str(subscription.uuid),
        payer_email=subscription.client.user.email,
    )
    subscription.external_id = preapproval.external_id
    subscription.provider_payload = {"preapproval": preapproval.payload}
    subscription.save(update_fields=["external_id", "provider_payload", "updated_at"])

    start, end = period_bounds(timezone.localdate())
    SubscriptionInvoice.objects.create(
        subscription=subscription,
        period_start=start,
        period_end=end,
        amount=subscription.price,
        method=PaymentMethod.CREDIT_CARD,
        status=InvoiceStatus.PENDING,
        due_date=start,
        provider=PaymentProvider.MERCADO_PAGO,
        external_id=preapproval.external_id,
        checkout_url=preapproval.init_point,
        provider_payload=preapproval.payload,
    )


def issue_pix_invoice(subscription: Subscription, *, period_start: date_cls) -> SubscriptionInvoice:
    """Emite (ou reemite) a cobrança PIX de um ciclo.

    Reemitir substitui a cobrança anterior em vez de criar um segundo mês: a
    antiga é marcada como expirada, respeitando a unicidade por ciclo.
    """
    start, end = period_bounds(period_start)

    SubscriptionInvoice.objects.filter(
        subscription=subscription, period_start=start, status=InvoiceStatus.PENDING
    ).update(status=InvoiceStatus.EXPIRED)

    minutes = _config("PIX_EXPIRATION_MINUTES")
    charge = mercadopago.get_client().create_pix_payment(
        amount=subscription.price,
        description=f"Sua Barbearia - {subscription.plan.name} ({start:%m/%Y})",
        external_reference=str(subscription.uuid),
        payer_email=subscription.client.user.email,
        payer_first_name=subscription.client.user.first_name,
        payer_last_name=subscription.client.user.last_name,
        expires_in_minutes=minutes,
    )

    return SubscriptionInvoice.objects.create(
        subscription=subscription,
        period_start=start,
        period_end=end,
        amount=subscription.price,
        method=PaymentMethod.PIX,
        status=InvoiceStatus.PENDING,
        due_date=start,
        provider=PaymentProvider.MERCADO_PAGO,
        external_id=charge.external_id,
        pix_qr_code=charge.qr_code,
        pix_qr_code_base64=charge.qr_code_base64,
        checkout_url=charge.ticket_url,
        expires_at=timezone.now() + timezone.timedelta(minutes=minutes),
        provider_payload=charge.payload,
    )


# ---------------------------------------------------------------------------
# Confirmar pagamento
# ---------------------------------------------------------------------------
@transaction.atomic
def confirm_invoice(
    invoice: SubscriptionInvoice,
    *,
    confirmed_by=None,
    provider_payload: dict | None = None,
) -> SubscriptionInvoice:
    """Marca a fatura como paga, lança a receita e estende o ciclo.

    Idempotente: um webhook reentregue não lança a receita duas vezes nem
    empurra o ciclo para frente de novo.
    """
    from apps.finance.models import TransactionCategory
    from apps.finance.services import record_income

    invoice = SubscriptionInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == InvoiceStatus.PAID:
        return invoice

    now = timezone.now()
    invoice.status = InvoiceStatus.PAID
    invoice.paid_at = now
    invoice.confirmed_by = confirmed_by
    if provider_payload:
        invoice.provider_payload = provider_payload

    subscription = invoice.subscription
    branch = subscription.branch or _fallback_branch(subscription)
    if branch is not None:
        invoice.transaction = record_income(
            branch=branch,
            amount=invoice.amount,
            description=(
                f"Plano {subscription.plan.name} - {subscription.client.full_name} "
                f"({invoice.period_start:%m/%Y})"
            ),
            category=TransactionCategory.SUBSCRIPTION,
            date=invoice.period_start,
            created_by=confirmed_by,
        )

    invoice.save(
        update_fields=[
            "status",
            "paid_at",
            "confirmed_by",
            "provider_payload",
            "transaction",
            "updated_at",
        ]
    )

    _activate(subscription, invoice)
    _notify_client(
        subscription,
        "Assinatura confirmada",
        f"Seu plano {subscription.plan.name} está ativo até "
        f"{subscription.current_period_end:%d/%m/%Y}.",
    )
    return invoice


def _activate(subscription: Subscription, invoice: SubscriptionInvoice) -> None:
    """Ativa a assinatura e move o ciclo para o período pago."""
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.current_period_start = invoice.period_start
    subscription.current_period_end = invoice.period_end
    if subscription.started_at is None:
        subscription.started_at = timezone.now()
    subscription.save(
        update_fields=[
            "status",
            "current_period_start",
            "current_period_end",
            "started_at",
            "updated_at",
        ]
    )


def _fallback_branch(subscription: Subscription):
    """Filial para lançar a receita quando o cliente não escolheu uma."""
    branch = subscription.plan.branches.first()
    if branch is not None:
        return branch
    return subscription.client.preferred_branch


# ---------------------------------------------------------------------------
# Cancelar
# ---------------------------------------------------------------------------
@transaction.atomic
def cancel_subscription(
    subscription: Subscription, *, immediate: bool = False, cancelled_by=None
) -> Subscription:
    """Cancela a assinatura.

    Por padrão o cliente mantém o benefício até o fim do ciclo já pago — ele
    pagou por ele. `immediate=True` corta na hora (uso do proprietário).
    """
    if subscription.status in (SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED):
        raise BusinessError("Esta assinatura já está encerrada.", code="SUBSCRIPTION_NOT_ACTIVE")

    if subscription.is_recurring and subscription.external_id:
        try:
            mercadopago.get_client().cancel_preapproval(subscription.external_id)
        except BusinessError as error:
            # O cancelamento local não pode ficar preso a uma falha do provedor;
            # a varredura periódica reconcilia o status depois.
            logger.warning(
                "Falha ao cancelar preapproval %s: %s", subscription.external_id, error.detail
            )

    now = timezone.now()
    keeps_benefit = (
        not immediate
        and subscription.status == SubscriptionStatus.ACTIVE
        and subscription.current_period_end is not None
        and subscription.current_period_end >= timezone.localdate()
    )

    if keeps_benefit:
        subscription.cancel_at_period_end = True
    else:
        subscription.status = SubscriptionStatus.CANCELLED
        subscription.cancelled_at = now

    subscription.save(
        update_fields=["status", "cancelled_at", "cancel_at_period_end", "updated_at"]
    )

    SubscriptionInvoice.objects.filter(
        subscription=subscription, status=InvoiceStatus.PENDING
    ).update(status=InvoiceStatus.CANCELLED)

    return subscription


# ---------------------------------------------------------------------------
# Consumo da cota
# ---------------------------------------------------------------------------
def consume_quota(*, coverage: Coverage, appointment, amount: Decimal) -> SubscriptionUsage | None:
    """Registra o uso de uma cota por um atendimento concluído.

    `get_or_create` na chave do atendimento garante que finalizar duas vezes
    não consuma dois usos.
    """
    if not coverage.is_covered or coverage.subscription is None:
        return None

    usage, _ = SubscriptionUsage.objects.get_or_create(
        appointment=appointment,
        defaults={
            "subscription": coverage.subscription,
            "service": appointment.service,
            "period_start": coverage.subscription.current_period_start,
            "covered_amount": Decimal(amount),
        },
    )
    return usage


# ---------------------------------------------------------------------------
# Manutenção (chamada pelas tasks do Celery)
# ---------------------------------------------------------------------------
def renew_pix_subscriptions(*, today: date_cls | None = None) -> dict[str, int]:
    """Emite a fatura do próximo ciclo das assinaturas PIX que vão vencer."""
    today = today or timezone.localdate()
    lead = _config("PIX_RENEWAL_LEAD_DAYS")
    horizon = today + timezone.timedelta(days=lead)

    pending = Subscription.objects.filter(
        billing_type=BillingType.PIX_MONTHLY,
        status=SubscriptionStatus.ACTIVE,
        cancel_at_period_end=False,
        current_period_end__lte=horizon,
        current_period_end__gte=today,
    ).select_related("plan", "client__user")

    issued = 0
    for subscription in pending:
        next_start = subscription.current_period_end + relativedelta(days=1)
        already = SubscriptionInvoice.objects.filter(
            subscription=subscription,
            period_start=next_start,
            status__in=(InvoiceStatus.PENDING, InvoiceStatus.PAID),
        ).exists()
        if already:
            continue
        try:
            invoice = issue_pix_invoice(subscription, period_start=next_start)
        except BusinessError as error:
            logger.warning(
                "Não foi possível emitir a fatura da assinatura %s: %s",
                subscription.uuid,
                error.detail,
            )
            continue
        issued += 1
        _notify_client(
            subscription,
            "Renovação do seu plano",
            f"O PIX de R$ {invoice.amount} para {invoice.period_start:%m/%Y} já está "
            "disponível no app.",
        )

    return {"issued": issued}


def suspend_overdue_subscriptions(*, today: date_cls | None = None) -> dict[str, int]:
    """Suspende quem passou do ciclo sem pagar, respeitando a tolerância."""
    today = today or timezone.localdate()
    grace = _config("GRACE_PERIOD_DAYS")
    deadline = today - timezone.timedelta(days=grace)

    overdue = Subscription.objects.filter(
        status=SubscriptionStatus.ACTIVE, current_period_end__lt=deadline
    )
    past_due = 0
    for subscription in overdue.select_related("plan", "client__user"):
        # Quem já cancelou no fim do ciclo apenas encerra.
        if subscription.cancel_at_period_end:
            subscription.status = SubscriptionStatus.EXPIRED
            subscription.save(update_fields=["status", "updated_at"])
            continue
        subscription.status = SubscriptionStatus.PAST_DUE
        subscription.save(update_fields=["status", "updated_at"])
        past_due += 1
        _notify_client(
            subscription,
            "Plano suspenso",
            f"Não identificamos o pagamento do plano {subscription.plan.name}. "
            "Regularize no app para voltar a usar os benefícios.",
        )

    expired = Subscription.objects.filter(
        status=SubscriptionStatus.PAST_DUE,
        current_period_end__lt=today - timezone.timedelta(days=grace + 30),
    ).update(status=SubscriptionStatus.EXPIRED, cancelled_at=timezone.now())

    return {"past_due": past_due, "expired": expired}


def sync_pending_invoices(*, limit: int = 100) -> dict[str, int]:
    """Rede de segurança para webhook perdido: consulta o provedor."""
    invoices = (
        SubscriptionInvoice.objects.filter(
            status=InvoiceStatus.PENDING,
            provider=PaymentProvider.MERCADO_PAGO,
            method=PaymentMethod.PIX,
        )
        .exclude(external_id="")
        .select_related("subscription__plan", "subscription__client__user")[:limit]
    )

    client = mercadopago.get_client()
    if not client.is_configured:
        return {"checked": 0, "confirmed": 0}

    confirmed = 0
    checked = 0
    for invoice in invoices:
        checked += 1
        try:
            data = client.get_payment(invoice.external_id)
        except BusinessError:
            continue
        status = str(data.get("status", ""))
        if status == "approved":
            confirm_invoice(invoice, provider_payload=data)
            confirmed += 1
        elif status in ("cancelled", "rejected", "expired"):
            SubscriptionInvoice.objects.filter(pk=invoice.pk).update(status=InvoiceStatus.EXPIRED)

    return {"checked": checked, "confirmed": confirmed}


def expire_stale_invoices(*, now=None) -> int:
    """Marca como expiradas as cobranças PIX cujo QR já venceu."""
    now = now or timezone.now()
    return SubscriptionInvoice.objects.filter(
        status=InvoiceStatus.PENDING, expires_at__lt=now
    ).update(status=InvoiceStatus.EXPIRED)


def _notify_client(subscription: Subscription, title: str, body: str) -> None:
    from apps.notifications.models import NotificationType
    from apps.notifications.services import create_notification

    try:
        create_notification(
            user=subscription.client.user,
            notification_type=NotificationType.SYSTEM,
            title=title,
            body=body,
            data={"subscription": str(subscription.uuid), "plan": subscription.plan.name},
        )
    except Exception:
        # Notificar nunca deve derrubar a cobrança já confirmada.
        logger.exception("Falha ao notificar a assinatura %s", subscription.uuid)


def subscriber_counts() -> dict[int, int]:
    """Assinantes ativos por plano — usado na listagem do proprietário."""
    rows = (
        Subscription.objects.filter(status=SubscriptionStatus.ACTIVE)
        .values("plan_id")
        .annotate(total=Count("id", filter=Q(status=SubscriptionStatus.ACTIVE)))
    )
    return {row["plan_id"]: row["total"] for row in rows}
