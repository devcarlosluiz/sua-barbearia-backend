"""Endpoints de avaliações."""

from __future__ import annotations

from django.db.models import Avg, Count, QuerySet
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.exceptions import BusinessError
from apps.core.permissions import IsAuthenticatedRole, IsOwner
from apps.reviews.models import Review
from apps.reviews.serializers import (
    ReviewCreateSerializer,
    ReviewReplySerializer,
    ReviewSerializer,
)


@extend_schema_view(
    list=extend_schema(tags=["Avaliações"], summary="Lista avaliações"),
    retrieve=extend_schema(tags=["Avaliações"], summary="Detalha uma avaliação"),
    create=extend_schema(
        tags=["Avaliações"],
        summary="Avalia um atendimento concluído",
        request=ReviewCreateSerializer,
        responses={201: ReviewSerializer},
    ),
)
class ReviewViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Avaliações de atendimento — uma por agendamento concluído."""

    permission_classes = [IsAuthenticatedRole]
    filterset_fields = ("barber", "branch", "rating", "is_published")
    ordering_fields = ("created_at", "rating")
    ordering = ("-created_at",)

    def get_serializer_class(self):
        return ReviewCreateSerializer if self.action == "create" else ReviewSerializer

    def get_queryset(self) -> QuerySet[Review]:
        queryset = Review.objects.select_related(
            "client__user", "barber__user", "branch", "appointment__service"
        )
        user = self.request.user
        if user.is_superuser or user.is_owner:
            return queryset
        if user.is_barber:
            barber = getattr(user, "barber_profile", None)
            return queryset.filter(barber=barber, is_published=True) if barber else queryset.none()
        client = getattr(user, "client_profile", None)
        return queryset.filter(client=client) if client else queryset.none()

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = ReviewCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        review = serializer.save()
        return Response(ReviewSerializer(review, context={"request": request}).data, status=201)

    @extend_schema(
        tags=["Avaliações"],
        summary="Responde uma avaliação (OWNER)",
        request=ReviewReplySerializer,
        responses={200: ReviewSerializer},
    )
    @action(detail=True, methods=["post"], permission_classes=[IsOwner])
    def reply(self, request: Request, pk: str | None = None) -> Response:
        review = self.get_object()
        serializer = ReviewReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review.reply = serializer.validated_data["reply"]
        review.replied_at = timezone.now()
        review.save(update_fields=["reply", "replied_at", "updated_at"])
        return Response(ReviewSerializer(review, context={"request": request}).data)

    @extend_schema(
        tags=["Avaliações"],
        summary="Resumo das avaliações (média e distribuição)",
        responses={200: None},
    )
    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        queryset = self.get_queryset().filter(is_published=True)
        barber_id = request.query_params.get("barber")
        if barber_id:
            queryset = queryset.filter(barber_id=barber_id)
        branch_id = request.query_params.get("branch")
        if branch_id:
            queryset = queryset.filter(branch_id=branch_id)

        aggregate = queryset.aggregate(average=Avg("rating"), total=Count("id"))
        distribution = list(
            queryset.values("rating").annotate(count=Count("id")).order_by("-rating")
        )
        return Response(
            {
                "average": round(aggregate["average"] or 0, 2),
                "total": aggregate["total"],
                "distribution": distribution,
            }
        )

    @extend_schema(
        tags=["Avaliações"],
        summary="Atendimentos concluídos ainda não avaliados",
        responses={200: None},
    )
    @action(detail=False, methods=["get"], url_path="pending")
    def pending(self, request: Request) -> Response:
        from apps.appointments.models import Appointment, AppointmentStatus
        from apps.appointments.serializers import AppointmentListSerializer

        client = getattr(request.user, "client_profile", None)
        if client is None:
            raise BusinessError(
                "Este usuário não possui perfil de cliente.",
                code="CLIENT_PROFILE_NOT_FOUND",
                status_code=404,
            )

        queryset = (
            Appointment.objects.with_relations()
            .filter(client=client, status=AppointmentStatus.COMPLETED, review__isnull=True)
            .order_by("-date")[:20]
        )
        return Response(
            AppointmentListSerializer(queryset, many=True, context={"request": request}).data
        )
