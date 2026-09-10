from django.contrib import admin

from apps.products.models import Product, ProductCategory


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "sku",
        "category",
        "cost_price",
        "sale_price",
        "total_stock",
        "is_active",
    )
    list_filter = ("is_active", "category")
    search_fields = ("name", "sku", "barcode")
    readonly_fields = ("uuid", "created_at", "updated_at")

    @admin.display(description="Estoque total")
    def total_stock(self, obj: Product) -> int:
        return obj.total_stock
