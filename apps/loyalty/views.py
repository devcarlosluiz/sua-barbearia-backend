"""Endpoints do programa de fidelidade."""

from __future__ import annotations

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.clients.models import Client
from apps.core.exceptions import BusinessError
from apps.core.mixins import AuditableViewSetMixin
from apps.core.permissions import IsAuthenticatedRole, IsOwner, IsOwnerOrReadOnly
from apps.loyalty.models import LoyaltyAccount, LoyaltyReward, LoyaltyTransaction
from apps.loyalty.serializers import (
    LoyaltyAccountSerializer,
    LoyaltyAdjustSerializer,
    LoyaltyRedeemSerializer,
    LoyaltyRewardSerializer,
    LoyaltyTransactionSerializer,
)
from apps.loyalty.services import adjust_points, get_or_create_account, redeem_reward


@extend_schema_view(
    list=extend_schema(tags=["Fidelidade"], summary="Lista contas de fidelidade (OWNER)"),
)
class LoyaltyAccountViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Contas de fidelidade. O cliente acessa a própria em `/loyalty/accounts/me/`."""

    serializer_class = LoyaltyAccountSerializer
    permission_classes = [IsAuthenticatedRole]
    filterset_fields = ("client",)
    ordering_fields = ("balance", "created_at")
    ordering = ("-balance",)

    def get_queryset(self) -> QuerySet[LoyaltyAccount]:
        queryset = LoyaltyAccount.objects.select_related("client__user", "client__preferred_branch")
        user = self.request.user
        if user.is_client:
            client = getattr(user, "client_profile", None)
            queryset = queryset.filter(client=client) if client else queryset.none()
        return queryset

    @extend_schema(
        tags=["Fidelidade"],
        summary="Extrato de fidelidade do cliente autenticado",
        responses={200: LoyaltyAccountSerializer},
    )
    @action(detail=False, methods=["get"])
    def me(self, request: Request) -> Response:
        client = getattr(request.user, "client_profile", None)
        if client is None:
            raise BusinessError(
                "Este usuário não possui perfil de cliente.",
                code="CLIENT_PROFILE_NOT_FOUND",
                status_code=404,
            )
        account = get_or_create_account(client)
        transactions = account.transactions.select_related("reward").order_by("-created_at")[:50]
        return Response(
            {
                "account": LoyaltyAccountSerializer(account, context={"request": request}).data,
                "transactions": LoyaltyTransactionSerializer(transactions, many=True).data,
            }
        )


@extend_schema_view(
    list=extend_schema(tags=["Fidelidade"], summary="Extrato de pontos"),
)
class LoyaltyTransactionViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = LoyaltyTransactionSerializer
    permission_classes = [IsAuthenticatedRole]
    filterset_fields = ("type", "account")
    ordering = ("-created_at",)

    def get_queryset(self) -> QuerySet[LoyaltyTransaction]:
        queryset = LoyaltyTransaction.objects.select_related("account__client__user", "reward")
        user = self.request.user
        if user.is_client:
            client = getattr(user, "client_profile", None)
            queryset = queryset.filter(account__client=client) if client else queryset.none()
        return queryset


@extend_schema_view(
    list=extend_schema(tags=["Fidelidade"], summary="Lista recompensas disponíveis"),
    create=extend_schema(tags=["Fidelidade"], summary="Cria uma recompensa (OWNER)"),
)
class LoyaltyRewardViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    serializer_class = LoyaltyRewardSerializer
    permission_classes = [IsOwnerOrReadOnly]
    filterset_fields = ("is_active", "type")
    ordering_fields = ("points_cost", "name")
    ordering = ("points_cost",)

    def get_queryset(self) -> QuerySet[LoyaltyReward]:
        queryset = LoyaltyReward.objects.select_related("service")
        user = self.request.user
        if user.is_authenticated and not (user.is_owner or user.is_superuser):
            queryset = queryset.filter(is_active=True)
        return queryset

    @extend_schema(
        tags=["Fidelidade"],
        summary="Resgata uma recompensa",
        request=LoyaltyRedeemSerializer,
        responses={200: LoyaltyTransactionSerializer},
    )
    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticatedRole])
    def redeem(self, request: Request) -> Response:
        serializer = LoyaltyRedeemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        client = self._resolve_client(request, data.get("client_id"))
        reward = LoyaltyReward.objects.filter(pk=data["reward_id"]).first()
        if reward is None:
            raise BusinessError(
                "Recompensa não encontrada.", code="REWARD_NOT_FOUND", status_code=404
            )

        movement = redeem_reward(client=client, reward=reward, created_by=request.user)
        return Response(LoyaltyTransactionSerializer(movement).data)

    @extend_schema(
        tags=["Fidelidade"],
        summary="Ajuste manual de pontos (OWNER)",
        request=LoyaltyAdjustSerializer,
        responses={200: LoyaltyTransactionSerializer},
    )
    @action(detail=False, methods=["post"], permission_classes=[IsOwner])
    def adjust(self, request: Request) -> Response:
        serializer = LoyaltyAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        client = Client.objects.filter(pk=data["client_id"]).first()
        if client is None:
            raise BusinessError("Cliente não encontrado.", code="CLIENT_NOT_FOUND", status_code=404)

        movement = adjust_points(
            client=client,
            points=data["points"],
            description=data["description"],
            created_by=request.user,
        )
        return Response(LoyaltyTransactionSerializer(movement).data)

    @staticmethod
    def _resolve_client(request: Request, client_id: int | None) -> Client:
        user = request.user
        if user.is_client:
            client = getattr(user, "client_profile", None)
            if client is None:
                raise BusinessError(
                    "Este usuário não possui perfil de cliente.",
                    code="CLIENT_PROFILE_NOT_FOUND",
                    status_code=404,
                )
            return client

        if client_id is None:
            raise BusinessError("Informe o cliente do resgate.", code="CLIENT_REQUIRED")
        client = Client.objects.filter(pk=client_id).first()
        if client is None:
            raise BusinessError("Cliente não encontrado.", code="CLIENT_NOT_FOUND", status_code=404)
        return client
