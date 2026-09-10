from django.contrib import admin

from apps.payments.models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "branch", "client", "amount", "method", "status", "paid_at")
    list_filter = ("status", "method", "provider", "branch")
    search_fields = ("uuid", "external_id", "client__user__first_name")
    date_hierarchy = "created_at"
    readonly_fields = ("uuid", "provider_payload", "created_at", "updated_at")
