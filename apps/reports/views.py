"""Endpoints de dashboards e relatórios."""

from __future__ import annotations

import hashlib

from django.core.cache import cache
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import BusinessError
from apps.core.permissions import IsBarber, IsClient, IsOwner
from apps.reports.services import (
    barber_dashboard,
    client_dashboard,
    owner_dashboard,
    resolve_period,
)

DASHBOARD_CACHE_SECONDS = 60

PERIOD_PARAMS = [
    OpenApiParameter(
        "period", str, description="today | 7d | 30d | this_month | last_month (padrão: 30d)"
    ),
    OpenApiParameter("start_date", str, description="AAAA-MM-DD"),
    OpenApiParameter("end_date", str, description="AAAA-MM-DD"),
]


def _period_from(request: Request):
    try:
        return resolve_period(
            request.query_params.get("period"),
            request.query_params.get("start_date"),
            request.query_params.get("end_date"),
        )
    except ValueError as exc:
        raise BusinessError("Datas inválidas. Use AAAA-MM-DD.", code="INVALID_DATE") from exc


@extend_schema(tags=["Relatórios"])
class OwnerDashboardView(APIView):
    """Painel do proprietário: KPIs, gráficos e rankings."""

    permission_classes = [IsOwner]

    @extend_schema(
        summary="Dashboard do proprietário",
        parameters=[
            *PERIOD_PARAMS,
            OpenApiParameter("branch", int),
            OpenApiParameter("barber", int),
            OpenApiParameter("service", int),
        ],
        responses={200: None},
    )
    def get(self, request: Request) -> Response:
        start, end = _period_from(request)
        branch_id = request.query_params.get("branch")
        barber_id = request.query_params.get("barber")
        service_id = request.query_params.get("service")

        cache_key = (
            "suabarbearia:dashboard:owner:"
            + hashlib.md5(f"{start}{end}{branch_id}{barber_id}{service_id}".encode()).hexdigest()
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        data = owner_dashboard(
            start=start,
            end=end,
            branch_id=int(branch_id) if branch_id else None,
            barber_id=int(barber_id) if barber_id else None,
            service_id=int(service_id) if service_id else None,
        )
        cache.set(cache_key, data, DASHBOARD_CACHE_SECONDS)
        return Response(data)


@extend_schema(tags=["Relatórios"])
class BarberDashboardView(APIView):
    """Painel do barbeiro: produção, comissão e avaliações."""

    permission_classes = [IsBarber]

    @extend_schema(summary="Dashboard do barbeiro", parameters=PERIOD_PARAMS, responses={200: None})
    def get(self, request: Request) -> Response:
        barber = getattr(request.user, "barber_profile", None)
        if barber is None:
            raise BusinessError(
                "Perfil de barbeiro não encontrado.",
                code="BARBER_PROFILE_NOT_FOUND",
                status_code=404,
            )
        start, end = _period_from(request)
        data = barber_dashboard(barber=barber, start=start, end=end)

        from django.utils import timezone

        from apps.appointments.models import BLOCKING_STATUSES, Appointment
        from apps.appointments.serializers import AppointmentListSerializer

        agenda = (
            Appointment.objects.with_relations()
            .filter(barber=barber, date=timezone.localdate())
            .order_by("start_time")
        )
        next_clients = (
            Appointment.objects.with_relations()
            .filter(
                barber=barber,
                date__gte=timezone.localdate(),
                status__in=BLOCKING_STATUSES,
            )
            .order_by("date", "start_time")[:5]
        )

        data["today_agenda"] = AppointmentListSerializer(
            agenda, many=True, context={"request": request}
        ).data
        data["next_clients"] = AppointmentListSerializer(
            next_clients, many=True, context={"request": request}
        ).data
        return Response(data)


@extend_schema(tags=["Relatórios"])
class ClientDashboardView(APIView):
    """Home do cliente: próximo agendamento, pontos e favoritos."""

    permission_classes = [IsClient]

    @extend_schema(summary="Dashboard do cliente", responses={200: None})
    def get(self, request: Request) -> Response:
        client = getattr(request.user, "client_profile", None)
        if client is None:
            raise BusinessError(
                "Perfil de cliente não encontrado.",
                code="CLIENT_PROFILE_NOT_FOUND",
                status_code=404,
            )

        raw = client_dashboard(client=client)

        from apps.appointments.serializers import AppointmentListSerializer
        from apps.barbers.serializers import BarberListSerializer
        from apps.branches.serializers import BranchListSerializer
        from apps.services.serializers import ServiceSummarySerializer

        context = {"request": request}
        next_appointment = raw.pop("next_appointment")
        preferred_branch = raw.pop("preferred_branch")
        preferred_barber = raw.pop("preferred_barber")
        favorite_service = raw.pop("favorite_service")

        raw["next_appointment"] = (
            AppointmentListSerializer(next_appointment, context=context).data
            if next_appointment
            else None
        )
        raw["preferred_branch"] = (
            BranchListSerializer(preferred_branch, context=context).data
            if preferred_branch
            else None
        )
        raw["preferred_barber"] = (
            BarberListSerializer(preferred_barber, context=context).data
            if preferred_barber
            else None
        )
        raw["favorite_service"] = (
            ServiceSummarySerializer(favorite_service, context=context).data
            if favorite_service
            else None
        )
        return Response(raw)
