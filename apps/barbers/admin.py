from django.contrib import admin

from apps.barbers.models import (
    Barber,
    BarberService,
    SpecialWorkingHour,
    TimeOff,
    WorkingHour,
)


class BarberServiceInline(admin.TabularInline):
    model = BarberService
    extra = 0
    autocomplete_fields = ("service",)


class WorkingHourInline(admin.TabularInline):
    model = WorkingHour
    extra = 0


@admin.register(Barber)
class BarberAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "commission_percentage", "rating", "is_active")
    list_filter = ("is_active", "branches")
    search_fields = ("user__first_name", "user__last_name", "user__email", "nickname")
    filter_horizontal = ("branches",)
    readonly_fields = ("uuid", "rating", "reviews_count", "created_at", "updated_at")
    inlines = [BarberServiceInline, WorkingHourInline]

    @admin.display(description="Barbeiro")
    def display_name(self, obj: Barber) -> str:
        return obj.display_name


@admin.register(TimeOff)
class TimeOffAdmin(admin.ModelAdmin):
    list_display = ("barber", "type", "starts_at", "ends_at", "reason")
    list_filter = ("type", "branch")
    search_fields = ("barber__user__first_name", "reason")
    date_hierarchy = "starts_at"


@admin.register(SpecialWorkingHour)
class SpecialWorkingHourAdmin(admin.ModelAdmin):
    list_display = ("barber", "branch", "date", "starts_at", "ends_at")
    list_filter = ("branch",)
    date_hierarchy = "date"
