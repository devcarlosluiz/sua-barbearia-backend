"""Tratamento das imagens enviadas pelos usuários (foto de perfil).

Uma foto vinda do celular chega com 3 a 8 MB e 4000 px de lado. Ela é exibida
em avatares de 38 a 56 px — guardar o original faria cada tela do app baixar
megabytes para desenhar um círculo de 40 px.

Por isso a foto é recortada em quadrado, reduzida e recomprimida no envio.
"""

from __future__ import annotations

import logging
import uuid
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile

logger = logging.getLogger("suabarbearia.application")

#: Limites de aceite. Folgados o bastante para qualquer foto de celular.
AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_MIN_SIDE = 64
AVATAR_MAX_SIDE = 6000
AVATAR_ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")

#: Lado do quadrado guardado. O maior uso é 56 px; 512 cobre telas densas e
#: eventuais ampliações futuras sem ficar pesado.
AVATAR_STORED_SIZE = 512

#: Qualidade do JPEG. Acima disso o ganho visual é imperceptível no tamanho
#: em que a foto aparece, mas o arquivo cresce bastante.
AVATAR_JPEG_QUALITY = 85


def normalize_avatar(arquivo, *, size: int = AVATAR_STORED_SIZE) -> SimpleUploadedFile:
    """Devolve a foto quadrada, reduzida e em JPEG.

    O recorte é central: é onde o rosto costuma estar, e o avatar é sempre
    desenhado em círculo — uma imagem retangular apareceria esticada ou cortada
    de forma imprevisível pelo widget.
    """
    from PIL import Image, ImageOps

    arquivo.seek(0)
    imagem = Image.open(arquivo)

    # Fotos de celular guardam a rotação em EXIF em vez de girar os pixels.
    # Sem isto, um retrato aparece deitado.
    imagem = ImageOps.exif_transpose(imagem)

    # JPEG não tem canal alfa: uma foto com transparência viraria fundo preto.
    if imagem.mode in ("RGBA", "LA", "P"):
        fundo = Image.new("RGB", imagem.size, (255, 255, 255))
        convertida = imagem.convert("RGBA")
        fundo.paste(convertida, mask=convertida.split()[-1])
        imagem = fundo
    elif imagem.mode != "RGB":
        imagem = imagem.convert("RGB")

    original = imagem.size
    # `fit` recorta e redimensiona em uma passada, mantendo o centro.
    imagem = ImageOps.fit(imagem, (size, size), method=Image.LANCZOS, centering=(0.5, 0.5))

    buffer = BytesIO()
    imagem.save(buffer, format="JPEG", quality=AVATAR_JPEG_QUALITY, optimize=True)
    buffer.seek(0)

    logger.info("Foto de perfil normalizada: %s -> %sx%s", original, size, size)

    # Nome único: com nome fixo o navegador continuaria exibindo a foto antiga.
    return SimpleUploadedFile(
        f"avatar-{uuid.uuid4().hex[:12]}.jpg",
        buffer.read(),
        content_type="image/jpeg",
    )


def validate_avatar_file(arquivo, error_class):
    """Valida o arquivo antes de decodificar a imagem inteira.

    `error_class` é injetada para o módulo não depender do DRF — a mensagem
    precisa dizer o que está errado ("a imagem tem 40x40 px"), não um genérico
    "arquivo inválido".
    """
    from PIL import Image

    if arquivo.size > AVATAR_MAX_BYTES:
        limite = AVATAR_MAX_BYTES // (1024 * 1024)
        atual = arquivo.size / (1024 * 1024)
        raise error_class(f"A imagem tem {atual:.1f} MB e o limite é {limite} MB.")

    try:
        imagem = Image.open(arquivo)
        imagem.verify()
        arquivo.seek(0)
        imagem = Image.open(arquivo)
    except Exception as error:
        # Qualquer falha de leitura significa arquivo inválido.
        raise error_class("Não foi possível ler a imagem. Envie um PNG, JPG ou WEBP.") from error

    if imagem.format not in AVATAR_ALLOWED_FORMATS:
        aceitos = ", ".join(AVATAR_ALLOWED_FORMATS)
        raise error_class(f"Formato {imagem.format}. Aceitos: {aceitos}.")

    largura, altura = imagem.size
    if largura < AVATAR_MIN_SIDE or altura < AVATAR_MIN_SIDE:
        raise error_class(
            f"A imagem tem {largura}x{altura} px. O mínimo é "
            f"{AVATAR_MIN_SIDE}x{AVATAR_MIN_SIDE} px."
        )
    if largura > AVATAR_MAX_SIDE or altura > AVATAR_MAX_SIDE:
        raise error_class(
            f"A imagem tem {largura}x{altura} px e o máximo é "
            f"{AVATAR_MAX_SIDE}x{AVATAR_MAX_SIDE} px."
        )

    arquivo.seek(0)
    return arquivo
