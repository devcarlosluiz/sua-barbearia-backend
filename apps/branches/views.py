"""Endpoints de filiais."""

from __future__ import annotations

from django.db.models import Count, Prefetch, QuerySet
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.branches.models import Branch, BranchHoliday, OpeningHour
from apps.branches.serializers import (
    BranchHolidaySerializer,
    BranchListSerializer,
    BranchSerializer,
    OpeningHourSerializer,
)
from apps.core.mixins import AuditableViewSetMixin, MultiSerializerMixin
from apps.core.permissions import IsOwnerOrReadOnly


@extend_schema(tags=["Filiais"])
class PublicBranchListView(APIView):
    """Filiais ativas visíveis sem autenticação.

    Usada na tela de cadastro, onde o cliente escolhe a filial preferida antes
    de ter um token. Expõe apenas informações públicas (endereço e contato).
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(
        summary="Lista pública de filiais ativas",
        responses={200: BranchListSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        queryset = Branch.objects.filter(is_active=True).order_by("name")
        return Response(
            BranchListSerializer(queryset, many=True, context={"request": request}).data
        )


@extend_schema_view(
    list=extend_schema(tags=["Filiais"], summary="Lista as filiais da Sua Barbearia"),
    retrieve=extend_schema(tags=["Filiais"], summary="Detalha uma filial"),
    create=extend_schema(tags=["Filiais"], summary="Cria uma filial (OWNER)"),
    update=extend_schema(tags=["Filiais"], summary="Atualiza uma filial (OWNER)"),
    partial_update=extend_schema(tags=["Filiais"], summary="Atualiza parcialmente (OWNER)"),
    destroy=extend_schema(tags=["Filiais"], summary="Remove uma filial (OWNER)"),
)
class BranchViewSet(AuditableViewSetMixin, MultiSerializerMixin, viewsets.ModelViewSet):
    """CRUD de filiais. Leitura liberada a qualquer autenticado; escrita só OWNER."""

    permission_classes = [IsOwnerOrReadOnly]
    serializer_class = BranchSerializer
    serializer_classes = {"list": BranchListSerializer}
    filterset_fields = ("is_active", "city", "state")
    search_fields = ("name", "city", "district")
    ordering_fields = ("name", "created_at")
    ordering = ("name",)

    def get_queryset(self) -> QuerySet[Branch]:
        queryset = Branch.objects.all().annotate(barbers_count=Count("barbers", distinct=True))
        if self.action in {"retrieve", "list"}:
            queryset = queryset.prefetch_related(
                Prefetch("opening_hours", queryset=OpeningHour.objects.order_by("weekday")),
                Prefetch(
                    "holidays",
                    queryset=BranchHoliday.objects.filter(date__gte=timezone.localdate()).order_by(
                        "date"
                    ),
                ),
            )
        user = self.request.user
        if user.is_authenticated and user.is_client:
            queryset = queryset.filter(is_active=True)
        return queryset

    @extend_schema(
        tags=["Filiais"],
        summary="Horários de funcionamento da filial",
        responses={200: OpeningHourSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="opening-hours")
    def opening_hours(self, request: Request, pk: str | None = None) -> Response:
        branch = self.get_object()
        hours = branch.opening_hours.order_by("weekday")
        return Response(OpeningHourSerializer(hours, many=True).data)

    @extend_schema(
        tags=["Filiais"],
        summary="Feriados e fechamentos da filial",
        request=BranchHolidaySerializer,
        responses={200: BranchHolidaySerializer(many=True), 201: BranchHolidaySerializer},
    )
    @action(detail=True, methods=["get", "post"])
    def holidays(self, request: Request, pk: str | None = None) -> Response:
        branch = self.get_object()
        if request.method == "POST":
            self.permission_denied_if_not_owner(request)
            serializer = BranchHolidaySerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(branch=branch)
            return Response(serializer.data, status=201)
        holidays = branch.holidays.order_by("date")
        return Response(BranchHolidaySerializer(holidays, many=True).data)

    def permission_denied_if_not_owner(self, request: Request) -> None:
        if not (request.user.is_superuser or request.user.is_owner):
            self.permission_denied(request, message="Apenas o proprietário pode alterar filiais.")
