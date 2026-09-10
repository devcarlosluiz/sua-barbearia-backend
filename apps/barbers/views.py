"""Endpoints de barbeiros, jornada de trabalho e ausências."""

from __future__ import annotations

from django.db.models import Prefetch, QuerySet
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.barbers.models import (
    Barber,
    BarberService,
    SpecialWorkingHour,
    TimeOff,
    WorkingHour,
)
from apps.barbers.serializers import (
    BarberListSerializer,
    BarberSerializer,
    BarberServiceSerializer,
    SpecialWorkingHourSerializer,
    TimeOffSerializer,
    WorkingHourSerializer,
)
from apps.core.exceptions import BusinessError
from apps.core.mixins import AuditableViewSetMixin, MultiSerializerMixin
from apps.core.permissions import IsOwner, IsOwnerOrBarber, IsOwnerOrReadOnly


def _current_barber(request: Request) -> Barber | None:
    return getattr(request.user, "barber_profile", None)


@extend_schema_view(
    list=extend_schema(
        tags=["Barbeiros"],
        summary="Lista barbeiros",
        parameters=[
            OpenApiParameter("branch", int, description="Filtra por filial"),
            OpenApiParameter("service", int, description="Filtra por serviço executado"),
        ],
    ),
    retrieve=extend_schema(tags=["Barbeiros"], summary="Detalha um barbeiro"),
    create=extend_schema(tags=["Barbeiros"], summary="Cadastra um barbeiro (OWNER)"),
    update=extend_schema(tags=["Barbeiros"], summary="Atualiza um barbeiro (OWNER)"),
    partial_update=extend_schema(tags=["Barbeiros"], summary="Atualiza parcialmente (OWNER)"),
    destroy=extend_schema(tags=["Barbeiros"], summary="Remove um barbeiro (OWNER)"),
)
class BarberViewSet(AuditableViewSetMixin, MultiSerializerMixin, viewsets.ModelViewSet):
    """CRUD de barbeiros. Escrita restrita ao OWNER."""

    serializer_class = BarberSerializer
    serializer_classes = {"list": BarberListSerializer}
    permission_classes = [IsOwnerOrReadOnly]
    filterset_fields = ("is_active", "branches")
    search_fields = ("user__first_name", "user__last_name", "nickname", "user__email")
    ordering_fields = ("user__first_name", "rating", "created_at")
    ordering = ("user__first_name",)

    def get_queryset(self) -> QuerySet[Barber]:
        queryset = (
            Barber.objects.select_related("user")
            .prefetch_related(
                "branches",
                Prefetch(
                    "barber_services",
                    queryset=BarberService.objects.select_related("service").filter(is_active=True),
                ),
            )
            .all()
        )
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch(
                    "working_hours",
                    queryset=WorkingHour.objects.select_related("branch").order_by("weekday"),
                )
            )

        user = self.request.user
        if user.is_authenticated and not (user.is_owner or user.is_superuser):
            queryset = queryset.filter(is_active=True, user__is_active=True)

        branch_id = self.request.query_params.get("branch")
        if branch_id:
            queryset = queryset.filter(branches__id=branch_id)

        service_id = self.request.query_params.get("service")
        if service_id:
            queryset = queryset.filter(
                barber_services__service_id=service_id, barber_services__is_active=True
            )
        return queryset.distinct()

    @extend_schema(
        tags=["Barbeiros"],
        summary="Serviços executados pelo barbeiro",
        responses={200: BarberServiceSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="services")
    def services(self, request: Request, pk: str | None = None) -> Response:
        barber = self.get_object()
        items = barber.barber_services.select_related("service").filter(is_active=True)
        return Response(BarberServiceSerializer(items, many=True).data)


@extend_schema_view(
    list=extend_schema(tags=["Barbeiros"], summary="Lista jornadas de trabalho"),
    create=extend_schema(tags=["Barbeiros"], summary="Cria jornada de trabalho (OWNER)"),
)
class WorkingHourViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Jornada semanal do barbeiro. Barbeiro lê a própria; OWNER gerencia todas."""

    serializer_class = WorkingHourSerializer
    permission_classes = [IsOwnerOrBarber]
    filterset_fields = ("barber", "branch", "weekday", "is_active")
    ordering = ("barber", "weekday", "starts_at")
    pagination_class = None

    def get_queryset(self) -> QuerySet[WorkingHour]:
        queryset = WorkingHour.objects.select_related("branch", "barber__user")
        user = self.request.user
        if user.is_barber:
            barber = _current_barber(self.request)
            queryset = queryset.filter(barber=barber) if barber else queryset.none()
        return queryset

    def _assert_can_write(self) -> None:
        user = self.request.user
        if not (user.is_owner or user.is_superuser):
            raise BusinessError(
                "Apenas o proprietário pode alterar jornadas de trabalho.",
                code="PERMISSION_DENIED",
                status_code=403,
            )

    def perform_create(self, serializer) -> None:
        self._assert_can_write()
        super().perform_create(serializer)

    def perform_update(self, serializer) -> None:
        self._assert_can_write()
        super().perform_update(serializer)

    def perform_destroy(self, instance) -> None:
        self._assert_can_write()
        super().perform_destroy(instance)


@extend_schema_view(
    list=extend_schema(tags=["Barbeiros"], summary="Lista horários especiais"),
)
class SpecialWorkingHourViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    serializer_class = SpecialWorkingHourSerializer
    permission_classes = [IsOwner]
    filterset_fields = ("barber", "branch", "date")
    ordering = ("-date",)

    def get_queryset(self) -> QuerySet[SpecialWorkingHour]:
        return SpecialWorkingHour.objects.select_related("branch", "barber__user")


@extend_schema_view(
    list=extend_schema(tags=["Barbeiros"], summary="Lista ausências e bloqueios"),
    create=extend_schema(tags=["Barbeiros"], summary="Registra ausência/bloqueio"),
)
class TimeOffViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Férias, folgas e bloqueios. O barbeiro pode gerenciar os próprios."""

    serializer_class = TimeOffSerializer
    permission_classes = [IsOwnerOrBarber]
    filterset_fields = ("barber", "branch", "type")
    ordering = ("-starts_at",)

    def get_queryset(self) -> QuerySet[TimeOff]:
        queryset = TimeOff.objects.select_related("barber__user", "branch")
        user = self.request.user
        if user.is_barber:
            barber = _current_barber(self.request)
            queryset = queryset.filter(barber=barber) if barber else queryset.none()
        return queryset

    def perform_create(self, serializer) -> None:
        user = self.request.user
        if user.is_barber:
            barber = _current_barber(self.request)
            if barber is None:
                raise BusinessError(
                    "Perfil de barbeiro não encontrado.", code="BARBER_PROFILE_NOT_FOUND"
                )
            if serializer.validated_data.get("barber") not in (None, barber):
                raise BusinessError(
                    "Você só pode registrar ausências para si mesmo.",
                    code="PERMISSION_DENIED",
                    status_code=403,
                )
            serializer.validated_data["barber"] = barber
        instance = serializer.save(created_by=user)
        from apps.core.services import audit

        audit.log_create(instance, user=user)
