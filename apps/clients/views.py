"""Endpoints de clientes."""

from __future__ import annotations

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.clients.models import Client
from apps.clients.serializers import (
    ClientListSerializer,
    ClientSelfUpdateSerializer,
    ClientSerializer,
)
from apps.core.exceptions import BusinessError
from apps.core.mixins import AuditableViewSetMixin, MultiSerializerMixin
from apps.core.permissions import IsAuthenticatedRole


@extend_schema_view(
    list=extend_schema(tags=["Clientes"], summary="Lista clientes (OWNER/BARBER)"),
    retrieve=extend_schema(tags=["Clientes"], summary="Detalha um cliente"),
    create=extend_schema(tags=["Clientes"], summary="Cadastra cliente pelo balcão"),
    update=extend_schema(tags=["Clientes"], summary="Atualiza um cliente"),
    partial_update=extend_schema(tags=["Clientes"], summary="Atualiza parcialmente"),
)
class ClientViewSet(AuditableViewSetMixin, MultiSerializerMixin, viewsets.ModelViewSet):
    """Gestão de clientes.

    - OWNER: acesso total.
    - BARBER: lê os clientes que já atendeu e pode cadastrar novos no balcão.
    - CLIENT: acessa apenas o próprio cadastro (`/clients/me/`).
    """

    serializer_class = ClientSerializer
    serializer_classes = {"list": ClientListSerializer}
    permission_classes = [IsAuthenticatedRole]
    filterset_fields = ("preferred_branch", "preferred_barber", "accepts_marketing")
    search_fields = ("user__first_name", "user__last_name", "user__email", "user__phone")
    ordering_fields = ("user__first_name", "total_spent", "total_visits", "last_visit_at")
    ordering = ("user__first_name",)

    def get_queryset(self) -> QuerySet[Client]:
        queryset = Client.objects.select_related(
            "user", "preferred_branch", "preferred_barber__user", "favorite_service"
        )
        user = self.request.user

        if user.is_superuser or user.is_owner:
            return queryset
        if user.is_barber:
            barber = getattr(user, "barber_profile", None)
            if barber is None:
                return queryset.none()
            return queryset.filter(appointments__barber=barber).distinct()
        return queryset.filter(user=user)

    def _assert_can_manage(self) -> None:
        user = self.request.user
        if not (user.is_superuser or user.is_owner or user.is_barber):
            raise BusinessError(
                "Você não tem permissão para gerenciar clientes.",
                code="PERMISSION_DENIED",
                status_code=403,
            )

    def perform_create(self, serializer) -> None:
        self._assert_can_manage()
        super().perform_create(serializer)

    def perform_destroy(self, instance) -> None:
        user = self.request.user
        if not (user.is_superuser or user.is_owner):
            raise BusinessError(
                "Apenas o proprietário pode remover clientes.",
                code="PERMISSION_DENIED",
                status_code=403,
            )
        super().perform_destroy(instance)

    @extend_schema(
        tags=["Clientes"],
        summary="Perfil do cliente autenticado",
        request=ClientSelfUpdateSerializer,
        responses={200: ClientSerializer},
    )
    @action(detail=False, methods=["get", "patch"])
    def me(self, request: Request) -> Response:
        client = getattr(request.user, "client_profile", None)
        if client is None:
            raise BusinessError(
                "Este usuário não possui perfil de cliente.",
                code="CLIENT_PROFILE_NOT_FOUND",
                status_code=404,
            )
        if request.method == "PATCH":
            serializer = ClientSelfUpdateSerializer(client, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            client.refresh_from_db()
        return Response(ClientSerializer(client, context={"request": request}).data)
