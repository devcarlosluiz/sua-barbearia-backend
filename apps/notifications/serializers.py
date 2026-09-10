"""Serializers de notificações."""

from __future__ import annotations

from rest_framework import serializers

from apps.notifications.models import DeviceToken, Notification


class NotificationSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source="get_type_display", read_only=True)
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = (
            "id",
            "uuid",
            "type",
            "type_display",
            "title",
            "body",
            "data",
            "appointment",
            "is_read",
            "read_at",
            "created_at",
        )
        read_only_fields = fields


class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ("id", "uuid", "token", "platform", "device_name", "is_active", "created_at")
        read_only_fields = ("id", "uuid", "created_at")


class MarkReadSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="Vazio marca todas as notificações como lidas.",
    )
