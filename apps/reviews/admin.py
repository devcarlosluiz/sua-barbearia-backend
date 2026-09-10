from django.contrib import admin

from apps.reviews.models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("created_at", "barber", "client", "rating", "is_published", "has_reply")
    list_filter = ("rating", "is_published", "branch", "barber")
    search_fields = ("comment", "client__user__first_name", "barber__user__first_name")
    date_hierarchy = "created_at"
    readonly_fields = ("uuid", "appointment", "client", "barber", "branch", "rating", "created_at")

    @admin.display(boolean=True, description="Respondida")
    def has_reply(self, obj: Review) -> bool:
        return bool(obj.reply)
