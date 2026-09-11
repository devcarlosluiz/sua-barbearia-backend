"""Rotas raiz do backend Sua Barbearia."""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.core.views import BrandingView, HealthCheckView

api_v1_patterns = [
    # Público: a tela de login precisa da logo antes de haver sessão.
    path("branding/", BrandingView.as_view(), name="branding"),
    path("auth/", include("apps.accounts.urls")),
    path("", include("apps.accounts.user_urls")),
    path("", include("apps.branches.urls")),
    path("", include("apps.services.urls")),
    path("", include("apps.barbers.urls")),
    path("", include("apps.clients.urls")),
    path("", include("apps.appointments.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.finance.urls")),
    path("", include("apps.products.urls")),
    path("", include("apps.inventory.urls")),
    path("", include("apps.loyalty.urls")),
    path("", include("apps.plans.urls")),
    path("", include("apps.reviews.urls")),
    path("", include("apps.notifications.urls")),
    path("", include("apps.reports.urls")),
]

urlpatterns = [
    # A raiz não serve conteúdo: este repositório é só o backend. Apontar para
    # a documentação evita que o domínio puro devolva um 404 sem explicação.
    #
    # Redirecionamento TEMPORÁRIO de propósito: quando o app Flutter for
    # publicado, a raiz passa a ser dele. Um 301 ficaria no cache do navegador
    # e continuaria mandando os usuários para o Swagger depois da troca.
    path("", RedirectView.as_view(url="/api/docs/", permanent=False), name="root"),
    path("admin/", admin.site.urls),
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("api/v1/", include((api_v1_patterns, "v1"), namespace="v1")),
    # --- Documentação ---
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

admin.site.site_header = "Sua Barbearia"
admin.site.site_title = "Sua Barbearia Admin"
admin.site.index_title = "Painel administrativo"
