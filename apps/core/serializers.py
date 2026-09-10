"""Serializers e mixins compartilhados."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers


class UniqueUserEmailMixin:
    """Valida unicidade de e-mail ignorando o usuário que está sendo editado.

    Os serializers aninhados de usuário (`user`) são reaproveitados na criação
    e na atualização de barbeiros e clientes. Sem excluir o próprio usuário da
    checagem, editar qualquer outro campo falha com "Já existe uma conta com
    este e-mail" — porque o e-mail enviado é justamente o dele.
    """

    def validate_email(self, value: str) -> str:
        from apps.accounts.models import User

        email = value.lower().strip()
        queryset = User.objects.filter(email=email)

        current_user_id = self._current_user_id()
        if current_user_id is not None:
            queryset = queryset.exclude(pk=current_user_id)

        if queryset.exists():
            raise serializers.ValidationError("Já existe uma conta com este e-mail.")
        return email

    def _current_user_id(self) -> Any | None:
        """`user_id` do objeto em edição (Barber/Client), quando houver."""
        parent = getattr(self, "parent", None)
        instance = getattr(parent, "instance", None)
        if instance is None:
            return None
        return getattr(instance, "user_id", None)


# ---------------------------------------------------------------------------
# Identidade visual
# ---------------------------------------------------------------------------
#: Formato recomendado ao proprietário. A logo é desenhada com **altura** fixa
#: (40 px na barra lateral, 52 px no login) e largura proporcional, então o que
#: importa é a proporção — algo perto de 3,75:1 encaixa sem sobras.
LOGO_RECOMMENDED_SIZE = (600, 160)

#: Limites de aceite. Generosos o bastante para qualquer logo real e apertados
#: o bastante para não deixar um arquivo de 20 MB ser baixado a cada abertura.
LOGO_MAX_BYTES = 2 * 1024 * 1024
LOGO_MIN_WIDTH = 120
LOGO_MIN_HEIGHT = 40
LOGO_MAX_SIDE = 3000
LOGO_ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")

#: Altura máxima guardada. A logo aparece em no máximo 52 px; 320 px cobre
#: telas de altíssima densidade com folga e mantém o arquivo leve.
LOGO_STORED_MAX_HEIGHT = 320


class BrandingSerializer(serializers.ModelSerializer):
    """Leitura da identidade visual. `logo_url` é absoluta para o app."""

    logo_url = serializers.SerializerMethodField()
    recommended_width = serializers.SerializerMethodField()
    recommended_height = serializers.SerializerMethodField()
    max_file_size_mb = serializers.SerializerMethodField()

    class Meta:
        from apps.core.models import Branding

        model = Branding
        fields = (
            "company_name",
            "logo_url",
            "recommended_width",
            "recommended_height",
            "max_file_size_mb",
            "updated_at",
        )
        read_only_fields = fields

    def get_logo_url(self, obj) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get("request")
        url = obj.logo.url
        return request.build_absolute_uri(url) if request else url

    def get_recommended_width(self, obj) -> int:
        return LOGO_RECOMMENDED_SIZE[0]

    def get_recommended_height(self, obj) -> int:
        return LOGO_RECOMMENDED_SIZE[1]

    def get_max_file_size_mb(self, obj) -> int:
        return LOGO_MAX_BYTES // (1024 * 1024)


class BrandingLogoUploadSerializer(serializers.Serializer):
    """Recebe e valida o arquivo da logo.

    A validação acontece aqui, e não só no modelo, para devolver ao
    proprietário uma mensagem que diz exatamente o que está errado — "a imagem
    tem 30 px de altura" ajuda; "arquivo inválido" não.
    """

    # `FileField`, e não `ImageField`: o `ImageField` do DRF decodifica a
    # imagem antes de qualquer validação nossa, então um arquivo de 20 MB
    # voltava como "envie uma imagem válida" em vez de "passou do limite".
    logo = serializers.FileField()

    def validate_logo(self, arquivo):
        if arquivo.size > LOGO_MAX_BYTES:
            limite = LOGO_MAX_BYTES // (1024 * 1024)
            atual = arquivo.size / (1024 * 1024)
            raise serializers.ValidationError(
                f"A imagem tem {atual:.1f} MB e o limite é {limite} MB."
            )

        from PIL import Image

        try:
            imagem = Image.open(arquivo)
            imagem.verify()
            arquivo.seek(0)
            imagem = Image.open(arquivo)
        except Exception as error:
            # Qualquer falha de leitura significa arquivo inválido.
            raise serializers.ValidationError(
                "Não foi possível ler a imagem. Envie um PNG, JPG ou WEBP."
            ) from error

        if imagem.format not in LOGO_ALLOWED_FORMATS:
            aceitos = ", ".join(LOGO_ALLOWED_FORMATS)
            raise serializers.ValidationError(f"Formato {imagem.format}. Aceitos: {aceitos}.")

        largura, altura = imagem.size
        if largura < LOGO_MIN_WIDTH or altura < LOGO_MIN_HEIGHT:
            raise serializers.ValidationError(
                f"A imagem tem {largura}x{altura} px. O mínimo é "
                f"{LOGO_MIN_WIDTH}x{LOGO_MIN_HEIGHT} px."
            )
        if largura > LOGO_MAX_SIDE or altura > LOGO_MAX_SIDE:
            raise serializers.ValidationError(
                f"A imagem tem {largura}x{altura} px e o máximo é "
                f"{LOGO_MAX_SIDE}x{LOGO_MAX_SIDE} px."
            )

        arquivo.seek(0)
        return arquivo


class BrandingNameSerializer(serializers.ModelSerializer):
    """Atualização do nome da barbearia."""

    class Meta:
        from apps.core.models import Branding

        model = Branding
        fields = ("company_name",)

    def validate_company_name(self, value: str) -> str:
        nome = value.strip()
        if len(nome) < 2:
            raise serializers.ValidationError("Informe ao menos 2 caracteres.")
        return nome
