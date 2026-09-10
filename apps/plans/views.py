"""Endpoints de planos mensais e assinaturas.

Escopo por papel (verificado no backend, nunca no app):

* **OWNER** — CRUD de planos, visão de todas as assinaturas e faturas,
  confirmação manual de pagamento no caixa, cancelamento imediato.
* **CLIENT** — vê os planos públicos, assina, consulta a própria assinatura e
  as próprias faturas, cancela a própria assinatura.
* **BARBER** — só leitura dos planos (para saber o que o cliente tem direito).
"""

from __future__ import annotations

import logging

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import BusinessError
from apps.core.mixins import AuditableViewSetMixin
from apps.core.permissions import IsAuthenticatedRole, IsOwner, IsOwnerOrReadOnly
from apps.plans.models import (
    InvoiceStatus,
    Plan,
    Subscription,
    SubscriptionInvoice,
    SubscriptionStatus,
)
from apps.plans.serializers import (
    CancelSubscriptionSerializer,
    ConfirmInvoiceSerializer,
    PlanListSerializer,
    PlanSerializer,
    SubscribeSerializer,
    SubscriptionInvoiceSerializer,
    SubscriptionSerializer,
)
from apps.plans.services import (
    cancel_subscription,
    confirm_invoice,
    issue_pix_invoice,
    live_subscription_for,
    subscribe,
    subscriber_counts,
)
from apps.plans.webhooks import handle_notification, signature_is_valid

logger = logging.getLogger("suabarbearia.application")


def _client_of(request: Request):
    """Perfil de cliente do usuário logado, ou erro claro se não houver."""
    client = getattr(request.user, "client_profile", None)
    if client is None:
        raise BusinessError(
            "Apenas clientes podem assinar um plano.",
            code="CLIENT_PROFILE_REQUIRED",
            status_code=403,
        )
    return client


@extend_schema_view(
    list=extend_schema(tags=["Planos"], summary="Lista planos mensais"),
    retrieve=extend_schema(tags=["Planos"], summary="Detalha um plano"),
    create=extend_schema(tags=["Planos"], summary="Cria um plano (OWNER)"),
    partial_update=extend_schema(tags=["Planos"], summary="Atualiza um plano (OWNER)"),
    destroy=extend_schema(tags=["Planos"], summary="Remove um plano (OWNER)"),
)
class PlanViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Planos mensais. O cliente só enxerga os públicos e ativos."""

    permission_classes = [IsOwnerOrReadOnly]
    filterset_fields = ("is_active", "is_public")
    search_fields = ("name", "description")
    ordering_fields = ("price", "name", "created_at")
    ordering = ("price",)

    def get_serializer_class(self):
        if self.action == "list":
            return PlanListSerializer
        return PlanSerializer

    def get_queryset(self) -> QuerySet[Plan]:
        queryset = Plan.objects.prefetch_related("branches", "plan_services__service")
        user = self.request.user
        if not (user.is_owner or user.is_superuser):
            # Cliente e barbeiro nunca veem plano oculto ou desativado.
            queryset = queryset.filter(is_active=True, is_public=True)
        return queryset

    def list(self, request: Request, *args, **kwargs) -> Response:
        """Anexa a contagem de assinantes para o proprietário."""
        response = super().list(request, *args, **kwargs)
        if not (request.user.is_owner or request.user.is_superuser):
            return response

        counts = subscriber_counts()
        payload = response.data
        rows = payload["results"] if isinstance(payload, dict) else payload
        for row in rows:
            row["subscribers_count"] = counts.get(row["id"], 0)
        return response

    def perform_destroy(self, instance: Plan) -> None:
        """Plano com assinante vivo não é apagado — é desativado.

        Excluir arrancaria o histórico financeiro de quem assinou.
        """
        has_subscribers = instance.subscriptions.filter(
            status__in=(
                SubscriptionStatus.PENDING_PAYMENT,
                SubscriptionStatus.ACTIVE,
                SubscriptionStatus.PAST_DUE,
            )
        ).exists()
        if has_subscribers:
            raise BusinessError(
                "Este plano tem assinantes ativos. Desative-o em vez de excluir.",
                code="PLAN_HAS_SUBSCRIBERS",
            )
        super().perform_destroy(instance)

    @extend_schema(
        tags=["Planos"],
        summary="Assinantes de um plano (OWNER)",
        responses={200: SubscriptionSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], permission_classes=[IsOwner])
    def subscribers(self, request: Request, pk: str | None = None) -> Response:
        plan = self.get_object()
        subscriptions = (
            plan.subscriptions.select_related("client__user", "plan", "branch")
            .prefetch_related("plan__plan_services__service", "invoices")
            .order_by("-created_at")
        )
        serializer = SubscriptionSerializer(
            subscriptions, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(tags=["Assinaturas"], summary="Lista assinaturas"),
    retrieve=extend_schema(tags=["Assinaturas"], summary="Detalha uma assinatura"),
)
class SubscriptionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Assinaturas. O cliente vê apenas a própria."""

    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticatedRole]
    filterset_fields = ("status", "plan", "billing_type", "client")
    ordering_fields = ("created_at", "current_period_end")
    ordering = ("-created_at",)

    def get_queryset(self) -> QuerySet[Subscription]:
        queryset = Subscription.objects.select_related(
            "client__user", "plan", "branch"
        ).prefetch_related("plan__plan_services__service", "plan__branches", "invoices")
        user = self.request.user
        if user.is_owner or user.is_superuser:
            return queryset
        client = getattr(user, "client_profile", None)
        if client is not None:
            return queryset.filter(client=client)
        if user.is_barber:
            # O barbeiro precisa apenas saber quem é assinante ativo.
            return queryset.filter(status=SubscriptionStatus.ACTIVE)
        return queryset.none()

    @extend_schema(
        tags=["Assinaturas"],
        summary="Minha assinatura (CLIENT)",
        responses={200: SubscriptionSerializer},
    )
    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticatedRole])
    def me(self, request: Request) -> Response:
        """Assinatura viva do cliente logado, ou `null` se não tiver."""
        client = _client_of(request)
        subscription = live_subscription_for(client)
        if subscription is None:
            return Response(None)
        serializer = self.get_serializer(subscription)
        return Response(serializer.data)

    @extend_schema(
        tags=["Assinaturas"],
        summary="Assinar um plano (CLIENT)",
        request=SubscribeSerializer,
        responses={201: SubscriptionSerializer},
    )
    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticatedRole])
    def subscribe(self, request: Request) -> Response:
        """Cria a assinatura e devolve os dados de pagamento do 1º ciclo.

        PIX vem com QR pronto; cartão vem com `checkout_url` — a página do
        Mercado Pago onde o cliente informa o cartão. O app nunca coleta
        número, validade ou CVV.
        """
        client = _client_of(request)
        serializer = SubscribeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        subscription = subscribe(
            client=client,
            plan=serializer.validated_data["plan"],
            billing_type=serializer.validated_data["billing_type"],
            branch=serializer.validated_data.get("branch"),
        )
        payload = SubscriptionSerializer(subscription, context=self.get_serializer_context()).data
        return Response(payload, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Assinaturas"],
        summary="Cancelar assinatura",
        request=CancelSubscriptionSerializer,
        responses={200: SubscriptionSerializer},
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticatedRole])
    def cancel(self, request: Request, pk: str | None = None) -> Response:
        subscription = self.get_object()
        user = request.user
        is_owner = user.is_owner or user.is_superuser
        if not is_owner:
            client = _client_of(request)
            if subscription.client_id != client.pk:
                raise BusinessError(
                    "Você só pode cancelar a sua própria assinatura.",
                    code="PERMISSION_DENIED",
                    status_code=403,
                )

        serializer = CancelSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Cortar o benefício de um ciclo já pago é decisão do proprietário.
        immediate = bool(serializer.validated_data["immediate"]) and is_owner

        subscription = cancel_subscription(subscription, immediate=immediate, cancelled_by=user)
        return Response(self.get_serializer(subscription).data)

    @extend_schema(
        tags=["Assinaturas"],
        summary="Gerar novo PIX do ciclo em aberto (CLIENT)",
        responses={200: SubscriptionInvoiceSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="renew-pix",
        permission_classes=[IsAuthenticatedRole],
    )
    def renew_pix(self, request: Request, pk: str | None = None) -> Response:
        """Reemite o QR quando o anterior expirou."""
        subscription = self.get_object()
        user = request.user
        if not (user.is_owner or user.is_superuser):
            client = _client_of(request)
            if subscription.client_id != client.pk:
                raise BusinessError(
                    "Você só pode pagar a sua própria assinatura.",
                    code="PERMISSION_DENIED",
                    status_code=403,
                )

        if subscription.is_recurring:
            raise BusinessError(
                "Esta assinatura é cobrada automaticamente no cartão.",
                code="SUBSCRIPTION_IS_RECURRING",
            )
        if subscription.status in (SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED):
            raise BusinessError("Esta assinatura está encerrada.", code="SUBSCRIPTION_NOT_ACTIVE")

        open_invoice = (
            subscription.invoices.filter(status=InvoiceStatus.PENDING)
            .order_by("period_start")
            .first()
        )
        period_start = (
            open_invoice.period_start
            if open_invoice is not None
            else _next_period_start(subscription)
        )
        invoice = issue_pix_invoice(subscription, period_start=period_start)
        return Response(
            SubscriptionInvoiceSerializer(invoice, context=self.get_serializer_context()).data
        )


def _next_period_start(subscription: Subscription):
    from dateutil.relativedelta import relativedelta
    from django.utils import timezone

    if subscription.current_period_end is None:
        return timezone.localdate()
    day_after = subscription.current_period_end + relativedelta(days=1)
    return max(day_after, timezone.localdate())


@extend_schema_view(
    list=extend_schema(tags=["Assinaturas"], summary="Lista faturas de assinatura"),
    retrieve=extend_schema(tags=["Assinaturas"], summary="Detalha uma fatura"),
)
class SubscriptionInvoiceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Faturas. O cliente vê apenas as próprias."""

    serializer_class = SubscriptionInvoiceSerializer
    permission_classes = [IsAuthenticatedRole]
    filterset_fields = ("status", "subscription", "method")
    ordering_fields = ("period_start", "due_date", "paid_at")
    ordering = ("-period_start",)

    def get_queryset(self) -> QuerySet[SubscriptionInvoice]:
        queryset = SubscriptionInvoice.objects.select_related(
            "subscription__plan", "subscription__client__user"
        )
        user = self.request.user
        if user.is_owner or user.is_superuser:
            return queryset
        client = getattr(user, "client_profile", None)
        if client is None:
            return queryset.none()
        return queryset.filter(subscription__client=client)

    @extend_schema(
        tags=["Assinaturas"],
        summary="Confirmar pagamento no caixa (OWNER)",
        request=ConfirmInvoiceSerializer,
        responses={200: SubscriptionInvoiceSerializer},
    )
    @action(detail=True, methods=["post"], permission_classes=[IsOwner])
    def confirm(self, request: Request, pk: str | None = None) -> Response:
        """Quita a fatura manualmente — cliente que pagou fora do app."""
        invoice = self.get_object()
        if invoice.status == InvoiceStatus.PAID:
            raise BusinessError("Esta fatura já está paga.", code="INVOICE_ALREADY_PAID")
        if invoice.status in (InvoiceStatus.CANCELLED, InvoiceStatus.REFUNDED):
            raise BusinessError("Esta fatura não pode ser confirmada.", code="INVOICE_NOT_PAYABLE")

        serializer = ConfirmInvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = confirm_invoice(invoice, confirmed_by=request.user)
        return Response(self.get_serializer(invoice).data)


@extend_schema(
    tags=["Assinaturas"],
    summary="Webhook do Mercado Pago",
    description=(
        "Endpoint público chamado pelo Mercado Pago. A autenticidade é "
        "verificada pela assinatura HMAC no header `x-signature`."
    ),
    request=None,
    responses={200: None},
)
class MercadoPagoWebhookView(APIView):
    """Notificações de pagamento e assinatura.

    Fica fora da autenticação por JWT de propósito: quem chama é o provedor.
    A defesa é a assinatura HMAC — nunca o corpo da requisição.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = None

    def post(self, request: Request) -> Response:
        body = request.data if isinstance(request.data, dict) else {}
        topic = str(body.get("type") or body.get("topic") or "")
        data_id = str((body.get("data") or {}).get("id") or body.get("data.id") or "")

        signature = request.headers.get("x-signature", "")
        request_id = request.headers.get("x-request-id", "")

        if not signature_is_valid(
            signature_header=signature, request_id=request_id, data_id=data_id
        ):
            # Não revelamos se o problema foi segredo ausente ou HMAC inválido.
            logger.warning(
                "Webhook do Mercado Pago recusado: assinatura invalida (topico=%s)", topic
            )
            return Response({"detail": "Assinatura inválida."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            result = handle_notification(topic=topic, data_id=data_id)
        except BusinessError as error:
            # Devolver 5xx faz o provedor reentregar, que é o que queremos
            # quando a falha é nossa (ou do próprio provedor).
            logger.warning("Webhook %s falhou: %s", topic, error.detail)
            return Response(
                {"detail": "Não foi possível processar agora."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(result, status=status.HTTP_200_OK)
