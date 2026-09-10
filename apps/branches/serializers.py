"""Serializers de filiais."""

from __future__ import annotations

from typing import Any

from django.utils.text import slugify
from rest_framework import serializers

from apps.branches.models import Branch, BranchHoliday, OpeningHour
from apps.core.utils import is_valid_cnpj, only_digits


class OpeningHourSerializer(serializers.ModelSerializer):
    weekday_display = serializers.CharField(source="get_weekday_display", read_only=True)

    class Meta:
        model = OpeningHour
        fields = ("id", "weekday", "weekday_display", "opens_at", "closes_at", "is_closed")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        opens_at = attrs.get("opens_at") or getattr(self.instance, "opens_at", None)
        closes_at = attrs.get("closes_at") or getattr(self.instance, "closes_at", None)
        if opens_at and closes_at and closes_at <= opens_at:
            raise serializers.ValidationError(
                {"closes_at": "O horário de fechamento deve ser maior que o de abertura."}
            )
        return attrs


class BranchHolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = BranchHoliday
        fields = ("id", "date", "description", "opens_at", "closes_at")


class BranchListSerializer(serializers.ModelSerializer):
    """Payload enxuto para listagens e seleção de filial pelo cliente."""

    cover_image_url = serializers.SerializerMethodField()
    full_address = serializers.CharField(read_only=True)

    class Meta:
        model = Branch
        fields = (
            "id",
            "uuid",
            "name",
            "slug",
            "city",
            "state",
            "district",
            "full_address",
            "phone",
            "whatsapp",
            "latitude",
            "longitude",
            "cover_image_url",
            "is_active",
        )

    def get_cover_image_url(self, obj: Branch) -> str | None:
        if not obj.cover_image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.cover_image.url) if request else obj.cover_image.url


class BranchSerializer(serializers.ModelSerializer):
    """Payload completo, usado no detalhe e na gestão pelo OWNER."""

    opening_hours = OpeningHourSerializer(many=True, required=False)
    holidays = BranchHolidaySerializer(many=True, read_only=True)
    full_address = serializers.CharField(read_only=True)
    cover_image_url = serializers.SerializerMethodField()
    barbers_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Branch
        fields = (
            "id",
            "uuid",
            "name",
            "slug",
            "cnpj",
            "address",
            "number",
            "complement",
            "district",
            "city",
            "state",
            "zip_code",
            "latitude",
            "longitude",
            "full_address",
            "phone",
            "whatsapp",
            "email",
            "cover_image",
            "cover_image_url",
            "slot_interval_minutes",
            "cancellation_limit_hours",
            "max_advance_booking_days",
            "loyalty_points_per_currency_unit",
            "loyalty_point_value",
            "is_active",
            "opening_hours",
            "holidays",
            "barbers_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "uuid", "slug", "created_at", "updated_at")
        extra_kwargs = {"cover_image": {"write_only": True, "required": False}}

    def get_cover_image_url(self, obj: Branch) -> str | None:
        if not obj.cover_image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.cover_image.url) if request else obj.cover_image.url

    def validate_cnpj(self, value: str) -> str:
        digits = only_digits(value)
        if digits and not is_valid_cnpj(digits):
            raise serializers.ValidationError("CNPJ inválido.")
        return digits

    def validate_zip_code(self, value: str) -> str:
        digits = only_digits(value)
        if digits and len(digits) != 8:
            raise serializers.ValidationError("O CEP deve ter 8 dígitos.")
        return digits

    def _sync_opening_hours(self, branch: Branch, hours: list[dict[str, Any]]) -> None:
        branch.opening_hours.all().delete()
        OpeningHour.objects.bulk_create([OpeningHour(branch=branch, **hour) for hour in hours])

    def create(self, validated_data: dict[str, Any]) -> Branch:
        hours = validated_data.pop("opening_hours", [])
        validated_data["slug"] = self._unique_slug(validated_data["name"])
        branch = Branch.objects.create(**validated_data)
        if hours:
            self._sync_opening_hours(branch, hours)
        return branch

    def update(self, instance: Branch, validated_data: dict[str, Any]) -> Branch:
        hours = validated_data.pop("opening_hours", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if hours is not None:
            self._sync_opening_hours(instance, hours)
        return instance

    @staticmethod
    def _unique_slug(name: str) -> str:
        base = slugify(name)[:130] or "filial"
        slug = base
        counter = 2
        while Branch.objects.filter(slug=slug).exists():
            slug = f"{base}-{counter}"
            counter += 1
        return slug
