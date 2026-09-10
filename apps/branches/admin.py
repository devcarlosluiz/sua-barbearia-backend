from django.contrib import admin

from apps.branches.models import Branch, BranchHoliday, OpeningHour


class OpeningHourInline(admin.TabularInline):
    model = OpeningHour
    extra = 0


class BranchHolidayInline(admin.TabularInline):
    model = BranchHoliday
    extra = 0


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "phone", "is_active")
    list_filter = ("is_active", "state", "city")
    search_fields = ("name", "city", "district", "cnpj")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("uuid", "created_at", "updated_at")
    inlines = [OpeningHourInline, BranchHolidayInline]
