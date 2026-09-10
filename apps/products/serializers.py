"""Serializers de produtos."""

from __future__ import annotations

from typing import Any

from django.utils.text import slugify
from rest_framework import serializers

from apps.products.models import Product, ProductCategory


class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ("id", "uuid", "name", "slug", "is_active", "created_at")
        read_only_fields = ("id", "uuid", "slug", "created_at")

    def create(self, validated_data: dict[str, Any]) -> ProductCategory:
        base = slugify(validated_data["name"])[:90] or "categoria"
        slug, counter = base, 2
        while ProductCategory.objects.filter(slug=slug).exists():
            slug = f"{base}-{counter}"
            counter += 1
        validated_data["slug"] = slug
        return super().create(validated_data)


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    image_url = serializers.SerializerMethodField()
    margin = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    stock_by_branch = serializers.SerializerMethodField()
    total_stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = (
            "id",
            "uuid",
            "name",
            "description",
            "sku",
            "barcode",
            "category",
            "category_name",
            "cost_price",
            "sale_price",
            "margin",
            "commission_percentage",
            "unit",
            "image",
            "image_url",
            "is_active",
            "total_stock",
            "stock_by_branch",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "uuid", "created_at", "updated_at")
        extra_kwargs = {"image": {"write_only": True, "required": False}}

    def get_image_url(self, obj: Product) -> str | None:
        if not obj.image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url

    def get_stock_by_branch(self, obj: Product) -> list[dict[str, Any]]:
        return [
            {
                "branch_id": item.branch_id,
                "branch_name": item.branch.name,
                "quantity": item.quantity,
                "minimum_stock": item.minimum_stock,
                "is_below_minimum": item.is_below_minimum,
            }
            for item in obj.stock_items.all()
        ]

    def validate_sku(self, value: str) -> str:
        sku = value.strip().upper()
        queryset = Product.objects.filter(sku=sku)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("Já existe um produto com este SKU.")
        return sku


class ProductSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ("id", "uuid", "name", "sku", "sale_price", "commission_percentage")
