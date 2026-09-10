"""Agregações dos dashboards e relatórios.

Todas as consultas são feitas no banco (nunca em Python) e o resultado do
dashboard do OWNER é cacheado por um curto período no Redis.
"""

from __future__ import annotations

from datetime import date as date_cls, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Avg, Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.appointments.models import Appointment, AppointmentStatus
from apps.clients.models import Client
from apps.finance.models import Commission, CommissionStatus, Transaction, TransactionType
from apps.inventory.models import SaleItem, SaleStatus
from apps.payments.models import Payment, PaymentStatus
from apps.reviews.models import Review

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=14, decimal_places=2)


def _money(expression) -> Any:
    return Coalesce(expression, ZERO, output_field=MONEY)


def resolve_period(
    period: str | None, start: str | None, end: str | None
) -> tuple[date_cls, date_cls]:
    """Converte os filtros do dashboard em um intervalo de datas."""
    today = timezone.localdate()
    if start or end:
        parsed_start = date_cls.fromisoformat(start) if start else today
        parsed_end = date_cls.fromisoformat(end) if end else today
        return parsed_start, parsed_end

    if period == "today":
        return today, today
    if period == "7d":
        return today - timedelta(days=6), today
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        first = today.replace(day=1)
        last_end = first - timedelta(days=1)
        return last_end.replace(day=1), last_end
    return today - timedelta(days=29), today


def _scope(queryset, branch_id: int | None, barber_id: int | None, service_id: int | None):
    if branch_id:
        queryset = queryset.filter(branch_id=branch_id)
    if barber_id:
        queryset = queryset.filter(barber_id=barber_id)
    if service_id:
        queryset = queryset.filter(service_id=service_id)
    return queryset


def owner_dashboard(
    *,
    start: date_cls,
    end: date_cls,
    branch_id: int | None = None,
    barber_id: int | None = None,
    service_id: int | None = None,
) -> dict[str, Any]:
    """KPIs, gráficos e rankings do painel do proprietário."""
    today = timezone.localdate()

    appointments = _scope(
        Appointment.objects.filter(date__gte=start, date__lte=end),
        branch_id,
        barber_id,
        service_id,
    )
    completed = appointments.filter(status=AppointmentStatus.COMPLETED)

    payments = Payment.objects.filter(
        status=PaymentStatus.PAID, paid_at__date__gte=start, paid_at__date__lte=end
    )
    if branch_id:
        payments = payments.filter(branch_id=branch_id)

    transactions = Transaction.objects.filter(date__gte=start, date__lte=end)
    if branch_id:
        transactions = transactions.filter(branch_id=branch_id)
    if barber_id:
        transactions = transactions.filter(barber_id=barber_id)

    revenue_today_qs = Transaction.objects.filter(date=today, type=TransactionType.INCOME)
    if branch_id:
        revenue_today_qs = revenue_today_qs.filter(branch_id=branch_id)

    month_start = today.replace(day=1)
    revenue_month_qs = Transaction.objects.filter(
        date__gte=month_start, date__lte=today, type=TransactionType.INCOME
    )
    if branch_id:
        revenue_month_qs = revenue_month_qs.filter(branch_id=branch_id)

    finance = transactions.aggregate(
        income=_money(Sum("amount", filter=Q(type=TransactionType.INCOME))),
        expense=_money(Sum("amount", filter=Q(type=TransactionType.EXPENSE))),
    )

    status_counts = appointments.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=AppointmentStatus.COMPLETED)),
        cancelled=Count("id", filter=Q(status=AppointmentStatus.CANCELLED)),
        no_show=Count("id", filter=Q(status=AppointmentStatus.NO_SHOW)),
    )

    completed_total = completed.aggregate(revenue=_money(Sum("price")), count=Count("id"))
    average_ticket = (
        (completed_total["revenue"] / completed_total["count"]).quantize(Decimal("0.01"))
        if completed_total["count"]
        else ZERO
    )

    new_clients = Client.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
    if branch_id:
        new_clients = new_clients.filter(preferred_branch_id=branch_id)

    products_sold = SaleItem.objects.filter(
        sale__status=SaleStatus.COMPLETED,
        sale__completed_at__date__gte=start,
        sale__completed_at__date__lte=end,
    )
    if branch_id:
        products_sold = products_sold.filter(sale__branch_id=branch_id)

    return {
        "period": {"start_date": start, "end_date": end},
        "kpis": {
            "revenue_today": revenue_today_qs.aggregate(total=_money(Sum("amount")))["total"],
            "revenue_month": revenue_month_qs.aggregate(total=_money(Sum("amount")))["total"],
            "revenue_period": finance["income"],
            "expense_period": finance["expense"],
            "balance_period": finance["income"] - finance["expense"],
            "appointments_today": _scope(
                Appointment.objects.filter(date=today), branch_id, barber_id, service_id
            ).count(),
            "appointments_period": status_counts["total"],
            "completed_appointments": status_counts["completed"],
            "cancelled_appointments": status_counts["cancelled"],
            "no_show_appointments": status_counts["no_show"],
            "average_ticket": average_ticket,
            "total_clients": Client.objects.count(),
            "new_clients": new_clients.count(),
            "products_sold": products_sold.aggregate(total=Coalesce(Sum("quantity"), 0))["total"],
            "products_revenue": products_sold.aggregate(total=_money(Sum("total")))["total"],
            "pending_commissions": Commission.objects.filter(
                status=CommissionStatus.PENDING,
                reference_date__gte=start,
                reference_date__lte=end,
            ).aggregate(total=_money(Sum("amount")))["total"],
        },
        "charts": {
            "revenue_by_day": list(
                transactions.filter(type=TransactionType.INCOME)
                .values("date")
                .annotate(total=_money(Sum("amount")))
                .order_by("date")
            ),
            "appointments_by_day": list(
                appointments.values("date")
                .annotate(
                    total=Count("id"),
                    completed=Count("id", filter=Q(status=AppointmentStatus.COMPLETED)),
                    cancelled=Count("id", filter=Q(status=AppointmentStatus.CANCELLED)),
                )
                .order_by("date")
            ),
            "top_services": list(
                completed.values("service__id", "service__name")
                .annotate(count=Count("id"), revenue=_money(Sum("price")))
                .order_by("-count")[:10]
            ),
            "top_barbers": list(
                completed.values("barber__id", "barber__user__first_name", "barber__nickname")
                .annotate(count=Count("id"), revenue=_money(Sum("price")))
                .order_by("-revenue")[:10]
            ),
            "revenue_by_branch": list(
                completed.values("branch__id", "branch__name")
                .annotate(count=Count("id"), revenue=_money(Sum("price")))
                .order_by("-revenue")
            ),
            "payment_methods": list(
                payments.values("method")
                .annotate(count=Count("id"), total=_money(Sum("amount")))
                .order_by("-total")
            ),
            "expenses_by_category": list(
                transactions.filter(type=TransactionType.EXPENSE)
                .values("category")
                .annotate(total=_money(Sum("amount")))
                .order_by("-total")
            ),
        },
    }


def barber_dashboard(*, barber, start: date_cls, end: date_cls) -> dict[str, Any]:
    """KPIs do painel do barbeiro."""
    today = timezone.localdate()
    month_start = today.replace(day=1)

    appointments = Appointment.objects.filter(barber=barber)
    today_qs = appointments.filter(date=today)
    period_qs = appointments.filter(date__gte=start, date__lte=end)

    completed_today = today_qs.filter(status=AppointmentStatus.COMPLETED)
    completed_month = appointments.filter(
        date__gte=month_start, date__lte=today, status=AppointmentStatus.COMPLETED
    )
    completed_period = period_qs.filter(status=AppointmentStatus.COMPLETED)

    commissions = Commission.objects.filter(barber=barber)

    return {
        "period": {"start_date": start, "end_date": end},
        "kpis": {
            "appointments_today": today_qs.exclude(
                status__in=[AppointmentStatus.CANCELLED]
            ).count(),
            "completed_today": completed_today.count(),
            "revenue_today": completed_today.aggregate(total=_money(Sum("price")))["total"],
            "appointments_month": completed_month.count(),
            "revenue_month": completed_month.aggregate(total=_money(Sum("price")))["total"],
            "revenue_period": completed_period.aggregate(total=_money(Sum("price")))["total"],
            "commission_month": commissions.filter(
                reference_date__gte=month_start, reference_date__lte=today
            ).aggregate(total=_money(Sum("amount")))["total"],
            "commission_pending": commissions.filter(status=CommissionStatus.PENDING).aggregate(
                total=_money(Sum("amount"))
            )["total"],
            "commission_paid": commissions.filter(status=CommissionStatus.PAID).aggregate(
                total=_money(Sum("amount"))
            )["total"],
            "rating": barber.rating,
            "reviews_count": barber.reviews_count,
            "clients_served": completed_period.values("client").distinct().count(),
        },
        "charts": {
            "revenue_by_day": list(
                completed_period.values("date")
                .annotate(total=_money(Sum("price")), count=Count("id"))
                .order_by("date")
            ),
            "top_services": list(
                completed_period.values("service__id", "service__name")
                .annotate(count=Count("id"), revenue=_money(Sum("price")))
                .order_by("-count")[:5]
            ),
        },
    }


def client_dashboard(*, client) -> dict[str, Any]:
    """Resumo da área do cliente."""
    from apps.appointments.models import BLOCKING_STATUSES

    today = timezone.localdate()
    upcoming = (
        Appointment.objects.with_relations()
        .filter(client=client, date__gte=today, status__in=BLOCKING_STATUSES)
        .order_by("date", "start_time")
    )
    history = Appointment.objects.filter(client=client, status=AppointmentStatus.COMPLETED)

    reviews = Review.objects.filter(client=client)

    return {
        "next_appointment": upcoming.first(),
        "upcoming_count": upcoming.count(),
        "total_visits": client.total_visits,
        "total_spent": client.total_spent,
        "average_ticket": client.average_ticket,
        "loyalty_points": client.loyalty_points,
        "last_visit_at": client.last_visit_at,
        "preferred_branch": client.preferred_branch,
        "preferred_barber": client.preferred_barber,
        "favorite_service": client.favorite_service,
        "pending_reviews": history.filter(review__isnull=True).count(),
        "reviews_given": reviews.count(),
        "average_rating_given": reviews.aggregate(average=Avg("rating"))["average"] or 0,
    }
