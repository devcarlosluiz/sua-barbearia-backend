"""Endpoints do financeiro: lançamentos, caixa e comissões."""

from __future__ import annotations

import csv
from datetime import date as date_cls, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, QuerySet, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import BusinessError
from apps.core.mixins import AuditableViewSetMixin
from apps.core.permissions import IsOwner, IsOwnerOrBarber
from apps.finance.models import Commission, CommissionStatus, Transaction, TransactionType
from apps.finance.serializers import (
    CashFlowSummarySerializer,
    CommissionPayoutSerializer,
    CommissionSerializer,
    TransactionSerializer,
    category_choices,
    type_choices,
)
from apps.finance.services import pay_commissions

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=14, decimal_places=2)


def parse_period(request: Request) -> tuple[date_cls, date_cls]:
    """Resolve o período a partir de `start_date`/`end_date` ou `period`."""
    today = timezone.localdate()
    period = request.query_params.get("period")
    start_raw = request.query_params.get("start_date")
    end_raw = request.query_params.get("end_date")

    if start_raw or end_raw:
        try:
            start = date_cls.fromisoformat(start_raw) if start_raw else today
            end = date_cls.fromisoformat(end_raw) if end_raw else today
        except ValueError as exc:
            raise BusinessError("Datas inválidas. Use AAAA-MM-DD.", code="INVALID_DATE") from exc
        if end < start:
            raise BusinessError(
                "A data final deve ser maior ou igual à inicial.", code="INVALID_PERIOD"
            )
        return start, end

    presets = {
        "today": (today, today),
        "7d": (today - timedelta(days=6), today),
        "30d": (today - timedelta(days=29), today),
        "this_month": (today.replace(day=1), today),
    }
    if period == "last_month":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    return presets.get(period, presets["30d"])


@extend_schema_view(
    list=extend_schema(tags=["Financeiro"], summary="Lista lançamentos"),
    create=extend_schema(tags=["Financeiro"], summary="Cria um lançamento"),
    retrieve=extend_schema(tags=["Financeiro"], summary="Detalha um lançamento"),
)
class TransactionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Receitas e despesas. Acesso exclusivo do OWNER."""

    serializer_class = TransactionSerializer
    permission_classes = [IsOwner]
    filterset_fields = ("branch", "type", "category", "barber", "is_automatic")
    search_fields = ("description", "notes")
    ordering_fields = ("date", "amount", "created_at")
    ordering = ("-date", "-created_at")

    def get_queryset(self) -> QuerySet[Transaction]:
        queryset = Transaction.objects.select_related("branch", "barber__user", "created_by")
        start = self.request.query_params.get("start_date")
        end = self.request.query_params.get("end_date")
        if start:
            queryset = queryset.filter(date__gte=start)
        if end:
            queryset = queryset.filter(date__lte=end)
        return queryset

    def perform_create(self, serializer) -> None:
        instance = serializer.save(created_by=self.request.user, is_automatic=False)
        from apps.core.services import audit

        audit.log_create(instance, user=self.request.user)

    @extend_schema(
        tags=["Financeiro"],
        summary="Resumo de caixa do período",
        parameters=[
            OpenApiParameter("period", str, description="today|7d|30d|this_month|last_month"),
            OpenApiParameter("start_date", str),
            OpenApiParameter("end_date", str),
            OpenApiParameter("branch", int),
        ],
        responses={200: CashFlowSummarySerializer},
    )
    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        start, end = parse_period(request)
        queryset = Transaction.objects.filter(date__gte=start, date__lte=end)
        branch_id = request.query_params.get("branch")
        if branch_id:
            queryset = queryset.filter(branch_id=branch_id)

        totals = queryset.aggregate(
            total_income=Coalesce(
                Sum("amount", filter=Q(type=TransactionType.INCOME)), ZERO, output_field=MONEY
            ),
            total_expense=Coalesce(
                Sum("amount", filter=Q(type=TransactionType.EXPENSE)), ZERO, output_field=MONEY
            ),
        )

        by_category = list(
            queryset.values("type", "category")
            .annotate(total=Coalesce(Sum("amount"), ZERO, output_field=MONEY), count=Count("id"))
            .order_by("-total")
        )

        from apps.payments.models import Payment, PaymentStatus

        payments = Payment.objects.filter(
            status=PaymentStatus.PAID, paid_at__date__gte=start, paid_at__date__lte=end
        )
        if branch_id:
            payments = payments.filter(branch_id=branch_id)
        by_payment_method = list(
            payments.values("method")
            .annotate(total=Coalesce(Sum("amount"), ZERO, output_field=MONEY), count=Count("id"))
            .order_by("-total")
        )

        daily = list(
            queryset.annotate(day=TruncDate("date"))
            .values("day", "type")
            .annotate(total=Coalesce(Sum("amount"), ZERO, output_field=MONEY))
            .order_by("day")
        )

        return Response(
            {
                "start_date": start,
                "end_date": end,
                "total_income": totals["total_income"],
                "total_expense": totals["total_expense"],
                "balance": totals["total_income"] - totals["total_expense"],
                "by_category": by_category,
                "by_payment_method": by_payment_method,
                "daily": daily,
            }
        )

    @extend_schema(
        tags=["Financeiro"],
        summary="Exporta os lançamentos do período em CSV",
        responses={200: None},
    )
    @action(detail=False, methods=["get"])
    def export(self, request: Request) -> HttpResponse:
        start, end = parse_period(request)
        queryset = (
            Transaction.objects.select_related("branch", "barber__user")
            .filter(date__gte=start, date__lte=end)
            .order_by("date")
        )
        branch_id = request.query_params.get("branch")
        if branch_id:
            queryset = queryset.filter(branch_id=branch_id)

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="sua-barbearia-financeiro-{start}-a-{end}.csv"'
        )
        response.write("﻿")  # BOM para o Excel abrir com acentuação correta

        writer = csv.writer(response, delimiter=";")
        writer.writerow(["Data", "Filial", "Tipo", "Categoria", "Descrição", "Barbeiro", "Valor"])
        for item in queryset:
            writer.writerow(
                [
                    item.date.strftime("%d/%m/%Y"),
                    item.branch.name,
                    item.get_type_display(),
                    item.get_category_display(),
                    item.description,
                    item.barber.display_name if item.barber else "",
                    f"{item.amount:.2f}".replace(".", ","),
                ]
            )
        return response


@extend_schema_view(
    list=extend_schema(tags=["Financeiro"], summary="Lista comissões"),
    retrieve=extend_schema(tags=["Financeiro"], summary="Detalha uma comissão"),
)
class CommissionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Comissões. O barbeiro vê apenas as próprias."""

    serializer_class = CommissionSerializer
    permission_classes = [IsOwnerOrBarber]
    filterset_fields = ("barber", "branch", "status", "reference_date")
    ordering_fields = ("reference_date", "amount", "created_at")
    ordering = ("-reference_date",)

    def get_queryset(self) -> QuerySet[Commission]:
        queryset = Commission.objects.select_related(
            "barber__user", "branch", "appointment__service", "appointment__client__user"
        )
        user = self.request.user
        if user.is_barber:
            barber = getattr(user, "barber_profile", None)
            queryset = queryset.filter(barber=barber) if barber else queryset.none()

        start = self.request.query_params.get("start_date")
        end = self.request.query_params.get("end_date")
        if start:
            queryset = queryset.filter(reference_date__gte=start)
        if end:
            queryset = queryset.filter(reference_date__lte=end)
        return queryset

    @extend_schema(
        tags=["Financeiro"],
        summary="Total de comissões por status no período",
        responses={200: None},
    )
    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        start, end = parse_period(request)
        queryset = self.get_queryset().filter(reference_date__gte=start, reference_date__lte=end)
        data = list(
            queryset.values("status")
            .annotate(total=Coalesce(Sum("amount"), ZERO, output_field=MONEY), count=Count("id"))
            .order_by("status")
        )
        total = sum((item["total"] for item in data), ZERO)
        return Response({"start_date": start, "end_date": end, "total": total, "by_status": data})

    @extend_schema(
        tags=["Financeiro"],
        summary="Paga comissões pendentes (OWNER)",
        request=CommissionPayoutSerializer,
        responses={200: None},
    )
    @action(detail=False, methods=["post"], permission_classes=[IsOwner], url_path="pay")
    def pay(self, request: Request) -> Response:
        serializer = CommissionPayoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = pay_commissions(
            commission_ids=serializer.validated_data["commission_ids"], created_by=request.user
        )
        return Response(result)


@extend_schema(tags=["Financeiro"])
class FinanceChoicesView(APIView):
    """Categorias e tipos de lançamento, para popular selects no frontend."""

    permission_classes = [IsOwner]

    @extend_schema(summary="Opções do financeiro", responses={200: None})
    def get(self, request: Request) -> Response:
        return Response(
            {
                "types": type_choices(),
                "categories": category_choices(),
                "commission_statuses": [
                    {"value": value, "label": str(label)}
                    for value, label in CommissionStatus.choices
                ],
            }
        )
