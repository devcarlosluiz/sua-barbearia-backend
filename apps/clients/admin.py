from django.contrib import admin

from apps.clients.models import Client


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "email",
        "phone",
        "preferred_branch",
        "total_visits",
        "total_spent",
        "loyalty_points",
        "last_visit_at",
    )
    list_filter = ("preferred_branch", "accepts_marketing")
    search_fields = ("user__first_name", "user__last_name", "user__email", "user__phone")
    autocomplete_fields = ("user", "preferred_branch", "preferred_barber", "favorite_service")
    readonly_fields = (
        "uuid",
        "loyalty_points",
        "total_visits",
        "total_spent",
        "last_visit_at",
        "favorite_service",
        "created_at",
        "updated_at",
    )
