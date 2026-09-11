"""Views utilitárias do núcleo."""

from __future__ import annotations

from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Branding
from apps.core.permissions import IsOwner
from apps.core.serializers import (
    BrandingLogoUploadSerializer,
    BrandingNameSerializer,
    BrandingSerializer,
)
from apps.core.services.branding import normalize_logo


class HealthCheckView(APIView):
    """Verificação de saúde usada por Docker/Kubernetes e monitoramento."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes: list = []

    @extend_schema(
        summary="Health check",
        description="Verifica conectividade com banco de dados e cache.",
        responses={200: None, 503: None},
    )
    def get(self, request: Request) -> Response:
        checks = {"database": self._check_database(), "cache": self._check_cache()}
        healthy = all(checks.values())
        return Response(
            {"status": "healthy" if healthy else "unhealthy", "checks": checks},
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @staticmethod
    def _check_database() -> bool:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
        except Exception:
            return False

    @staticmethod
    def _check_cache() -> bool:
        try:
            cache.set("suabarbearia:healthcheck", "ok", 10)
            return cache.get("suabarbearia:healthcheck") == "ok"
        except Exception:
            return False


# O `responses` de cada método não é enfeite: sendo esta uma `APIView` pura,
# sem `queryset` nem `serializer_class`, o drf-spectacular não tem como inferir
# o que sai daqui. Sem a declaração ele registra "unable to guess serializer" e
# DESCARTA a view — os endpoints de identidade visual sumiam da documentação.
@extend_schema_view(
    get=extend_schema(
        tags=["Identidade visual"],
        summary="Logo do sistema (público)",
        description=(
            "Devolve a logo exibida no aplicativo. É público porque a tela de "
            "login precisa da logo antes de existir sessão. Quando não há logo "
            "enviada, `logo_url` vem nulo e o app usa a marca padrão."
        ),
        responses=BrandingSerializer,
    ),
    put=extend_schema(
        tags=["Identidade visual"],
        summary="Enviar logo (OWNER)",
        request={"multipart/form-data": BrandingLogoUploadSerializer},
        responses=BrandingSerializer,
    ),
    patch=extend_schema(
        tags=["Identidade visual"],
        summary="Alterar o nome da barbearia (OWNER)",
        request=BrandingNameSerializer,
        responses=BrandingSerializer,
    ),
    delete=extend_schema(
        tags=["Identidade visual"],
        summary="Voltar à logo padrão (OWNER)",
        responses=BrandingSerializer,
    ),
)
class BrandingView(APIView):
    """Logo do sistema: leitura pública, escrita só do proprietário."""

    # `JSONParser` para o PATCH do nome; os demais para o upload da logo.
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        # A leitura é aberta (tela de login); a escrita é do proprietário.
        if self._is_public_read():
            return [AllowAny()]
        return [IsOwner()]

    def get_authenticators(self):
        # Sem isto, um token expirado faria o GET público falhar com 401 e a
        # tela de login ficaria sem logo justamente para quem precisa entrar.
        if self._is_public_read():
            return []
        return super().get_authenticators()

    def _is_public_read(self) -> bool:
        """Verdadeiro apenas quando há uma requisição real e ela é um GET.

        O DRF chama `get_authenticators()` de dentro de `initialize_request()`,
        que roda ANTES de `self.request` ser atribuído no `dispatch()`. Numa
        requisição HTTP isso passa despercebido porque o `setup()` do Django já
        preencheu `self.request`.

        O drf-spectacular não segue esse caminho: ele chama
        `initialize_request()` direto, com `view.request = None`. Ler
        `.method` sem checagem derrubava a geração inteira do schema, e
        `/api/schema/` respondia 500 — deixando o Swagger sem carregar.

        Sem requisição, o caminho seguro é o restritivo: autentica e exige
        proprietário.
        """
        request = getattr(self, "request", None)
        return request is not None and request.method == "GET"

    def get(self, request: Request) -> Response:
        branding = Branding.load()
        return Response(BrandingSerializer(branding, context={"request": request}).data)

    def put(self, request: Request) -> Response:
        serializer = BrandingLogoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        branding = Branding.load()
        if branding.logo:
            # Trocar a logo não pode deixar o arquivo antigo ocupando disco.
            branding.logo.delete(save=False)

        branding.logo = normalize_logo(serializer.validated_data["logo"])
        branding.updated_by = request.user
        branding.save(update_fields=["logo", "updated_by", "updated_at"])

        return Response(BrandingSerializer(branding, context={"request": request}).data)

    def patch(self, request: Request) -> Response:
        branding = Branding.load()
        serializer = BrandingNameSerializer(branding, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(BrandingSerializer(branding, context={"request": request}).data)

    def delete(self, request: Request) -> Response:
        branding = Branding.load()
        branding.clear_logo()
        branding.updated_by = request.user
        branding.save(update_fields=["updated_by", "updated_at"])
        return Response(BrandingSerializer(branding, context={"request": request}).data)
