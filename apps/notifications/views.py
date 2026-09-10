"""Endpoints de notificações e tokens de dispositivo."""

from __future__ import annotations

from django.db.models import QuerySet
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.notifications.models import DeviceToken, Notification
from apps.notifications.serializers import (
    DeviceTokenSerializer,
    MarkReadSerializer,
    NotificationSerializer,
)


@extend_schema_view(
    list=extend_schema(tags=["Notificações"], summary="Lista as notificações do usuário"),
)
class NotificationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Notificações do usuário autenticado."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ("type",)
    ordering = ("-created_at",)

    def get_queryset(self) -> QuerySet[Notification]:
        queryset = Notification.objects.filter(user=self.request.user)
        unread = self.request.query_params.get("unread")
        if unread == "true":
            queryset = queryset.filter(read_at__isnull=True)
        return queryset

    @extend_schema(
        tags=["Notificações"],
        summary="Quantidade de notificações não lidas",
        responses={200: None},
    )
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request: Request) -> Response:
        count = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
        return Response({"unread_count": count})

    @extend_schema(
        tags=["Notificações"],
        summary="Marca notificações como lidas",
        request=MarkReadSerializer,
        responses={200: None},
    )
    @action(detail=False, methods=["post"], url_path="mark-read")
    def mark_read(self, request: Request) -> Response:
        serializer = MarkReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data.get("ids")

        queryset = Notification.objects.filter(user=request.user, read_at__isnull=True)
        if ids:
            queryset = queryset.filter(pk__in=ids)
        updated = queryset.update(read_at=timezone.now())
        return Response({"updated": updated})


@extend_schema_view(
    list=extend_schema(tags=["Notificações"], summary="Lista tokens de push do usuário"),
    create=extend_schema(tags=["Notificações"], summary="Registra um token de push"),
)
class DeviceTokenViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Tokens FCM/APNs do usuário, usados para push notification."""

    serializer_class = DeviceTokenSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    queryset = DeviceToken.objects.none()  # o queryset real depende do usuário

    def get_queryset(self) -> QuerySet[DeviceToken]:
        return DeviceToken.objects.filter(user=self.request.user, is_active=True)

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        token, _ = DeviceToken.objects.update_or_create(
            token=data["token"],
            defaults={
                "user": request.user,
                "platform": data["platform"],
                "device_name": data.get("device_name", ""),
                "is_active": True,
                "last_used_at": timezone.now(),
            },
        )
        return Response(DeviceTokenSerializer(token).data, status=201)

    def perform_destroy(self, instance: DeviceToken) -> None:
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
