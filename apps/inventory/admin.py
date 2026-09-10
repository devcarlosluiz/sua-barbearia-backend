from django.contrib import admin

from apps.inventory.models import Sale, SaleItem, StockItem, StockMovement


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "quantity", "minimum_stock", "updated_at")
    list_filter = ("branch",)
    search_fields = ("product__name", "product__sku")
    readonly_fields = ("quantity",)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "product",
        "branch",
        "type",
        "quantity",
        "previous_quantity",
        "new_quantity",
    )
    list_filter = ("type", "branch")
    search_fields = ("product__name", "reason")
    date_hierarchy = "created_at"
    readonly_fields = tuple(f.name for f in StockMovement._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    readonly_fields = ("total",)


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("created_at", "branch", "client", "barber", "total", "status")
    list_filter = ("status", "branch")
    search_fields = ("uuid", "client__user__first_name")
    date_hierarchy = "created_at"
    readonly_fields = ("uuid", "subtotal", "total", "created_at", "updated_at")
    inlines = [SaleItemInline]
