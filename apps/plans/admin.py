"""Admin de planos e assinaturas."""

from __future__ import annotations

from django.contrib import admin

from apps.plans.models import (
    Plan,
    PlanService,
    Subscription,
    SubscriptionInvoice,
    SubscriptionUsage,
)


class PlanServiceInline(admin.TabularInline):
    model = PlanService
    extra = 1
    autocomplete_fields = ("service",)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "overage_discount_percentage", "is_active", "is_public")
    list_filter = ("is_active", "is_public")
    search_fields = ("name", "description")
    filter_horizontal = ("branches",)
    inlines = (PlanServiceInline,)


class SubscriptionInvoiceInline(admin.TabularInline):
    model = SubscriptionInvoice
    extra = 0
    fields = ("period_start", "period_end", "amount", "method", "status", "paid_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "client",
        "plan",
        "status",
        "billing_type",
        "price",
        "current_period_end",
    )
    list_filter = ("status", "billing_type", "plan")
    search_fields = ("client__user__email", "client__user__first_name", "external_id")
    autocomplete_fields = ("client", "plan")
    readonly_fields = ("external_id", "provider_payload", "started_at", "cancelled_at")
    inlines = (SubscriptionInvoiceInline,)


@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = ("subscription", "period_start", "amount", "method", "status", "paid_at")
    list_filter = ("status", "method", "provider")
    search_fields = ("subscription__client__user__email", "external_id")
    readonly_fields = (
        "provider_payload",
        "pix_qr_code",
        "pix_qr_code_base64",
        "checkout_url",
    )


@admin.register(SubscriptionUsage)
class SubscriptionUsageAdmin(admin.ModelAdmin):
    list_display = ("subscription", "service", "period_start", "covered_amount", "used_at")
    list_filter = ("period_start", "service")
    search_fields = ("subscription__client__user__email",)
