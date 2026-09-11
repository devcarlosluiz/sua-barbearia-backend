"""Rotas de autenticação (prefixo: /api/v1/auth/)."""

from __future__ import annotations

from django.urls import path

from apps.accounts.views import (
    ChangePasswordView,
    ForgotPasswordView,
    GoogleAuthView,
    LoginView,
    LogoutView,
    MeAvatarView,
    MeView,
    RefreshView,
    RegisterView,
    ResetPasswordView,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("google/", GoogleAuthView.as_view(), name="auth-google"),
    path("refresh/", RefreshView.as_view(), name="auth-refresh"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("forgot-password/", ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("reset-password/", ResetPasswordView.as_view(), name="auth-reset-password"),
    path("change-password/", ChangePasswordView.as_view(), name="auth-change-password"),
    path("me/", MeView.as_view(), name="auth-me"),
    path("me/avatar/", MeAvatarView.as_view(), name="auth-me-avatar"),
]
