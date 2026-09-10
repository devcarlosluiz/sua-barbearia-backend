from django.contrib import admin

from apps.core.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "entity", "entity_id", "ip_address")
    list_filter = ("action", "entity", "created_at")
    search_fields = ("entity", "entity_id", "request_id", "user__email")
    readonly_fields = tuple(f.name for f in AuditLog._meta.fields)
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
