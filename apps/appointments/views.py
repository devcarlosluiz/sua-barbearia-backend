"""Endpoints de agendamento, agenda e atendimento."""

from __future__ import annotations

from django.db.models import Prefetch, QuerySet
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status as http_status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.appointments.filters import AppointmentFilter
from apps.appointments.models import Appointment, AppointmentStatus, AppointmentStatusHistory
from apps.appointments.serializers import (
    AppointmentCancelSerializer,
    AppointmentCompleteSerializer,
    AppointmentCreateSerializer,
    AppointmentDetailSerializer,
    AppointmentListSerializer,
    AppointmentRescheduleSerializer,
    AppointmentStatusChangeSerializer,
    AvailableSlotsQuerySerializer,
    AvailableSlotsResponseSerializer,
)
from apps.appointments.services import booking
from apps.appointments.services.availability import build_availability
from apps.clients.models import Client
from apps.core.exceptions import BusinessError
from apps.core.permissions import IsAuthenticatedRole


@extend_schema(tags=["Agendamentos"])
class AvailableSlotsView(APIView):
    """Horários livres de um barbeiro para um serviço em uma data."""

    permission_classes = [IsAuthenticatedRole]

    @extend_schema(
        summary="Consulta horários disponíveis",
        parameters=[
            OpenApiParameter("branch_id", int, required=True),
            OpenApiParameter("barber_id", int, required=True),
            OpenApiParameter("service_id", int, required=True),
            OpenApiParameter("date", str, required=True, description="AAAA-MM-DD"),
        ],
        responses={200: AvailableSlotsResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        query = AvailableSlotsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        availability = build_availability(
            data["branch_id"], data["barber_id"], data["service_id"], data["date"]
        )
        slots = availability.available_slots()

        return Response(
            {
                "date": data["date"],
                "branch_id": data["branch_id"],
                "barber_id": data["barber_id"],
                "service_id": data["service_id"],
                "duration_minutes": availability.duration_minutes(),
                "price": availability.price(),
                "slots": slots,
            }
        )


@extend_schema_view(
    list=extend_schema(tags=["Agendamentos"], summary="Lista agendamentos"),
    retrieve=extend_schema(tags=["Agendamentos"], summary="Detalha um agendamento"),
    create=extend_schema(
        tags=["Agendamentos"],
        summary="Cria um agendamento",
        request=AppointmentCreateSerializer,
        responses={201: AppointmentDetailSerializer},
    ),
)
class AppointmentViewSet(viewsets.ModelViewSet):
    """Agendamentos.

    O queryset é sempre recortado pelo papel do usuário:
    OWNER vê tudo, BARBER vê a própria agenda, CLIENT vê os próprios horários.
    """

    permission_classes = [IsAuthenticatedRole]
    serializer_class = AppointmentDetailSerializer
    filterset_class = AppointmentFilter
    search_fields = (
        "client__user__first_name",
        "client__user__last_name",
        "client__user__phone",
        "service__name",
    )
    ordering_fields = ("date", "start_time", "created_at")
    ordering = ("-date", "-start_time")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return AppointmentListSerializer
        if self.action == "create":
            return AppointmentCreateSerializer
        return AppointmentDetailSerializer

    def get_queryset(self) -> QuerySet[Appointment]:
        queryset = Appointment.objects.with_relations()
        if self.action in {"retrieve", "partial_update"}:
            queryset = queryset.prefetch_related(
                Prefetch(
                    "status_history",
                    queryset=AppointmentStatusHistory.objects.select_related("changed_by"),
                )
            )

        user = self.request.user
        if user.is_superuser or user.is_owner:
            return queryset
        if user.is_barber:
            barber = getattr(user, "barber_profile", None)
            return queryset.filter(barber=barber) if barber else queryset.none()
        client = getattr(user, "client_profile", None)
        return queryset.filter(client=client) if client else queryset.none()

    # ------------------------------------------------------------------
    # Criação
    # ------------------------------------------------------------------
    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = AppointmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        client = self._resolve_client(request, data.get("client_id"))
        auto_confirm = request.user.is_owner or request.user.is_barber or request.user.is_superuser

        appointment = booking.create_appointment(
            client=client,
            branch_id=data["branch_id"],
            barber_id=data["barber_id"],
            service_id=data["service_id"],
            day=data["date"],
            start_time=data["start_time"],
            notes=data.get("notes", ""),
            created_by=request.user,
            auto_confirm=auto_confirm,
        )
        return Response(
            AppointmentDetailSerializer(appointment, context={"request": request}).data,
            status=http_status.HTTP_201_CREATED,
        )

    def _resolve_client(self, request: Request, client_id: int | None) -> Client:
        user = request.user
        if user.is_client:
            client = getattr(user, "client_profile", None)
            if client is None:
                raise BusinessError(
                    "Seu perfil de cliente não foi encontrado.",
                    code="CLIENT_PROFILE_NOT_FOUND",
                    status_code=404,
                )
            return client

        if client_id is None:
            raise BusinessError("Informe o cliente do agendamento.", code="CLIENT_REQUIRED")
        client = Client.objects.select_related("user").filter(pk=client_id).first()
        if client is None:
            raise BusinessError("Cliente não encontrado.", code="CLIENT_NOT_FOUND", status_code=404)
        return client

    # ------------------------------------------------------------------
    # Ações do ciclo de vida
    # ------------------------------------------------------------------
    @extend_schema(
        tags=["Agendamentos"],
        summary="Cancela um agendamento",
        request=AppointmentCancelSerializer,
        responses={200: AppointmentDetailSerializer},
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: str | None = None) -> Response:
        appointment = self.get_object()
        serializer = AppointmentCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appointment = booking.cancel_appointment(
            appointment=appointment,
            user=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(AppointmentDetailSerializer(appointment, context={"request": request}).data)

    @extend_schema(
        tags=["Agendamentos"],
        summary="Reagenda um agendamento",
        request=AppointmentRescheduleSerializer,
        responses={200: AppointmentDetailSerializer},
    )
    @action(detail=True, methods=["post"])
    def reschedule(self, request: Request, pk: str | None = None) -> Response:
        appointment = self.get_object()
        serializer = AppointmentRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if request.user.is_client and appointment.client_id != getattr(
            getattr(request.user, "client_profile", None), "pk", None
        ):
            raise BusinessError(
                "Você só pode remarcar os seus próprios agendamentos.",
                code="PERMISSION_DENIED",
                status_code=403,
            )

        appointment = booking.reschedule_appointment(
            appointment=appointment,
            day=data["date"],
            start_time=data["start_time"],
            user=request.user,
            barber_id=data.get("barber_id"),
            reason=data.get("reason", ""),
        )
        return Response(AppointmentDetailSerializer(appointment, context={"request": request}).data)

    @extend_schema(
        tags=["Agendamentos"],
        summary="Altera o status do agendamento",
        request=AppointmentStatusChangeSerializer,
        responses={200: AppointmentDetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request: Request, pk: str | None = None) -> Response:
        appointment = self.get_object()
        self._assert_staff(request)
        serializer = AppointmentStatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appointment = booking.transition_status(
            appointment=appointment,
            to_status=serializer.validated_data["status"],
            user=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(AppointmentDetailSerializer(appointment, context={"request": request}).data)

    @extend_schema(
        tags=["Atendimento"],
        summary="Registra a chegada do cliente",
        responses={200: AppointmentDetailSerializer},
    )
    @action(detail=True, methods=["post"])
    def arrive(self, request: Request, pk: str | None = None) -> Response:
        return self._simple_transition(request, AppointmentStatus.ARRIVED)

    @extend_schema(
        tags=["Atendimento"],
        summary="Inicia o atendimento",
        responses={200: AppointmentDetailSerializer},
    )
    @action(detail=True, methods=["post"])
    def start(self, request: Request, pk: str | None = None) -> Response:
        return self._simple_transition(request, AppointmentStatus.IN_PROGRESS)

    @extend_schema(
        tags=["Atendimento"],
        summary="Finaliza o atendimento e registra o pagamento",
        request=AppointmentCompleteSerializer,
        responses={200: AppointmentDetailSerializer},
    )
    @action(detail=True, methods=["post"])
    def complete(self, request: Request, pk: str | None = None) -> Response:
        appointment = self.get_object()
        self._assert_staff(request)
        serializer = AppointmentCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        appointment = booking.complete_appointment(
            appointment=appointment,
            user=request.user,
            payment_method=data["payment_method"],
            amount=data.get("amount"),
            discount_amount=data.get("discount_amount") or 0,
            notes=data.get("notes", ""),
        )
        return Response(AppointmentDetailSerializer(appointment, context={"request": request}).data)

    @extend_schema(
        tags=["Atendimento"],
        summary="Marca o cliente como faltante",
        responses={200: AppointmentDetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show(self, request: Request, pk: str | None = None) -> Response:
        return self._simple_transition(request, AppointmentStatus.NO_SHOW)

    # ------------------------------------------------------------------
    # Agendas
    # ------------------------------------------------------------------
    @extend_schema(
        tags=["Agendamentos"],
        summary="Agenda do dia",
        parameters=[
            OpenApiParameter("date", str, description="AAAA-MM-DD (padrão: hoje)"),
            OpenApiParameter("branch_id", int),
            OpenApiParameter("barber_id", int),
        ],
        responses={200: AppointmentListSerializer(many=True)},
    )
    @action(detail=False, methods=["get"])
    def agenda(self, request: Request) -> Response:
        day = request.query_params.get("date")
        target = timezone.localdate()
        if day:
            from datetime import date as date_cls

            try:
                target = date_cls.fromisoformat(day)
            except ValueError as exc:
                raise BusinessError("Data inválida. Use AAAA-MM-DD.", code="INVALID_DATE") from exc

        queryset = self.get_queryset().filter(date=target).order_by("start_time")
        branch_id = request.query_params.get("branch_id")
        if branch_id:
            queryset = queryset.filter(branch_id=branch_id)
        barber_id = request.query_params.get("barber_id")
        if barber_id:
            queryset = queryset.filter(barber_id=barber_id)

        return Response(
            {
                "date": target,
                "count": queryset.count(),
                "appointments": AppointmentListSerializer(
                    queryset, many=True, context={"request": request}
                ).data,
            }
        )

    @extend_schema(
        tags=["Agendamentos"],
        summary="Próximos agendamentos do usuário autenticado",
        responses={200: AppointmentListSerializer(many=True)},
    )
    @action(detail=False, methods=["get"])
    def upcoming(self, request: Request) -> Response:
        from apps.appointments.models import BLOCKING_STATUSES

        queryset = (
            self.get_queryset()
            .filter(date__gte=timezone.localdate(), status__in=BLOCKING_STATUSES)
            .order_by("date", "start_time")[:20]
        )
        return Response(
            AppointmentListSerializer(queryset, many=True, context={"request": request}).data
        )

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------
    def _simple_transition(self, request: Request, to_status: str) -> Response:
        appointment = self.get_object()
        self._assert_staff(request)
        appointment = booking.transition_status(
            appointment=appointment, to_status=to_status, user=request.user
        )
        return Response(AppointmentDetailSerializer(appointment, context={"request": request}).data)

    def _assert_staff(self, request: Request) -> None:
        user = request.user
        if not (user.is_superuser or user.is_owner or user.is_barber):
            raise BusinessError(
                "Apenas a equipe da barbearia pode executar esta ação.",
                code="PERMISSION_DENIED",
                status_code=403,
            )

    @extend_schema(
        tags=["Agendamentos"],
        summary="Exclui um agendamento",
        description=(
            "Apaga o registro. O proprietário exclui qualquer agendamento não concluído; "
            "o cliente exclui os próprios, dentro do prazo de cancelamento da filial. "
            "Para manter o histórico, use o cancelamento."
        ),
        responses={204: None},
    )
    def destroy(self, request: Request, *args, **kwargs) -> Response:
        # Quem pode excluir é regra de negócio e vive no service — o queryset já
        # recorta por papel, mas isso sozinho deixaria o barbeiro apagar a
        # própria agenda.
        booking.delete_appointment(appointment=self.get_object(), user=request.user)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Agendamentos"])
class AppointmentHistoryView(APIView):
    """Histórico de atendimentos concluídos do usuário autenticado."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Histórico de atendimentos",
        responses={200: AppointmentListSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        queryset = Appointment.objects.with_relations().filter(status=AppointmentStatus.COMPLETED)
        user = request.user
        if user.is_client:
            client = getattr(user, "client_profile", None)
            queryset = queryset.filter(client=client) if client else queryset.none()
        elif user.is_barber:
            barber = getattr(user, "barber_profile", None)
            queryset = queryset.filter(barber=barber) if barber else queryset.none()

        queryset = queryset.order_by("-date", "-start_time")[:100]
        return Response(
            AppointmentListSerializer(queryset, many=True, context={"request": request}).data
        )
