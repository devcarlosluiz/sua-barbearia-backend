from django.contrib import admin

from apps.appointments.models import Appointment, AppointmentStatusHistory


class StatusHistoryInline(admin.TabularInline):
    model = AppointmentStatusHistory
    extra = 0
    readonly_fields = ("from_status", "to_status", "changed_by", "reason", "created_at")

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "start_time",
        "client",
        "barber",
        "service",
        "branch",
        "status",
        "price",
    )
    list_filter = ("status", "branch", "date")
    search_fields = (
        "client__user__first_name",
        "client__user__last_name",
        "client__user__phone",
        "uuid",
    )
    date_hierarchy = "date"
    autocomplete_fields = ("client", "barber", "branch", "service")
    readonly_fields = (
        "uuid",
        "confirmed_at",
        "arrived_at",
        "started_at",
        "completed_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    inlines = [StatusHistoryInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("client__user", "barber__user", "branch", "service")
        )
