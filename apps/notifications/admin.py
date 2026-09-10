from django.contrib import admin

from apps.notifications.models import DeviceToken, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "type", "title", "read_at")
    list_filter = ("type",)
    search_fields = ("title", "body", "user__email")
    date_hierarchy = "created_at"
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "device_name", "is_active", "last_used_at")
    list_filter = ("platform", "is_active")
    search_fields = ("user__email", "token")
