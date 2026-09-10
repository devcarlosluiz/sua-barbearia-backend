"""Serializers do catálogo de serviços."""

from __future__ import annotations

from typing import Any

from django.utils.text import slugify
from rest_framework import serializers

from apps.services.models import Service, ServiceCategory


class ServiceCategorySerializer(serializers.ModelSerializer):
    services_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ServiceCategory
        fields = (
            "id",
            "uuid",
            "name",
            "slug",
            "display_order",
            "is_active",
            "services_count",
            "created_at",
        )
        read_only_fields = ("id", "uuid", "slug", "created_at")

    def create(self, validated_data: dict[str, Any]) -> ServiceCategory:
        validated_data["slug"] = _unique_slug(ServiceCategory, validated_data["name"])
        return super().create(validated_data)


class ServiceSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = (
            "id",
            "uuid",
            "name",
            "slug",
            "description",
            "category",
            "category_name",
            "duration_minutes",
            "price",
            "image",
            "image_url",
            "display_order",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "uuid", "slug", "created_at", "updated_at")
        extra_kwargs = {"image": {"write_only": True, "required": False}}

    def get_image_url(self, obj: Service) -> str | None:
        if not obj.image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url

    def create(self, validated_data: dict[str, Any]) -> Service:
        validated_data["slug"] = _unique_slug(Service, validated_data["name"])
        return super().create(validated_data)


class ServiceSummarySerializer(serializers.ModelSerializer):
    """Usado dentro de agendamentos e listagens compostas."""

    class Meta:
        model = Service
        fields = ("id", "uuid", "name", "duration_minutes", "price")


def _unique_slug(model: type, name: str) -> str:
    base = slugify(name)[:130] or "item"
    slug = base
    counter = 2
    while model.objects.filter(slug=slug).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug
