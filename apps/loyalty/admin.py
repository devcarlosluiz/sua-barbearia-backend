from django.contrib import admin

from apps.loyalty.models import LoyaltyAccount, LoyaltyReward, LoyaltyTransaction


@admin.register(LoyaltyAccount)
class LoyaltyAccountAdmin(admin.ModelAdmin):
    list_display = ("client", "balance", "lifetime_earned", "lifetime_redeemed")
    search_fields = ("client__user__first_name", "client__user__email")
    readonly_fields = ("uuid", "balance", "lifetime_earned", "lifetime_redeemed")


@admin.register(LoyaltyTransaction)
class LoyaltyTransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "account", "type", "points", "balance_after", "description")
    list_filter = ("type",)
    search_fields = ("account__client__user__first_name", "description")
    date_hierarchy = "created_at"
    readonly_fields = tuple(f.name for f in LoyaltyTransaction._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(LoyaltyReward)
class LoyaltyRewardAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "points_cost", "discount_value", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("name",)
