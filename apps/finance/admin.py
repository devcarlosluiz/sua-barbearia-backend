from django.contrib import admin

from apps.finance.models import Commission, Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "branch", "type", "category", "description", "amount", "is_automatic")
    list_filter = ("type", "category", "branch", "is_automatic")
    search_fields = ("description", "notes")
    date_hierarchy = "date"
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(Commission)
class CommissionAdmin(admin.ModelAdmin):
    list_display = (
        "reference_date",
        "barber",
        "branch",
        "base_amount",
        "percentage",
        "amount",
        "status",
    )
    list_filter = ("status", "branch", "barber")
    search_fields = ("barber__user__first_name",)
    date_hierarchy = "reference_date"
    readonly_fields = ("uuid", "created_at", "updated_at")
