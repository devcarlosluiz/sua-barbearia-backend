from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.accounts.models import PasswordResetToken, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "full_name", "role", "phone", "is_active", "is_verified")
    list_filter = ("role", "is_active", "is_verified", "is_staff")
    search_fields = ("email", "first_name", "last_name", "phone")
    ordering = ("first_name", "last_name")
    readonly_fields = ("uuid", "created_at", "updated_at", "last_login")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Dados pessoais", {"fields": ("first_name", "last_name", "phone", "avatar")}),
        ("Perfil", {"fields": ("role", "is_verified")}),
        (
            "Permissões",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Datas", {"fields": ("uuid", "last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "role", "password1", "password2"),
            },
        ),
    )


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires_at", "used_at")
    search_fields = ("user__email",)
    readonly_fields = ("user", "token", "expires_at", "used_at", "created_at")

    def has_add_permission(self, request) -> bool:
        return False
