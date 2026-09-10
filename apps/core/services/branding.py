"""Normalização da logo enviada pelo proprietário.

A logo é baixada por **todo** cliente em **toda** abertura do app, inclusive na
tela de login. Guardar o arquivo original (que pode ter 2 MB e 3000 px) faria o
app carregar mais devagar sem nenhum ganho visual: ela nunca é desenhada com
mais de 52 px de altura.

Por isso o arquivo é redimensionado no envio, uma única vez, para uma altura
que ainda cobre telas de altíssima densidade — e convertido para PNG, que
preserva a transparência necessária para a logo funcionar nos temas claro e
escuro.
"""

from __future__ import annotations

import logging
import uuid
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.serializers import LOGO_STORED_MAX_HEIGHT

logger = logging.getLogger("suabarbearia.application")


def normalize_logo(arquivo, *, max_height: int = LOGO_STORED_MAX_HEIGHT) -> SimpleUploadedFile:
    """Devolve a logo em PNG, com no máximo `max_height` px de altura.

    Imagens menores que o limite passam sem reamostragem — ampliar uma logo
    pequena só borraria o traço.
    """
    from PIL import Image

    arquivo.seek(0)
    imagem = Image.open(arquivo)

    # `RGBA` mantém a transparência; sem isso a logo ganha um fundo preto ao
    # ser convertida a partir de um PNG com canal alfa.
    if imagem.mode not in ("RGBA", "LA"):
        imagem = imagem.convert("RGBA")

    imagem = _trim_transparent_margin(imagem)

    largura, altura = imagem.size
    if altura > max_height:
        nova_largura = max(1, round(largura * max_height / altura))
        imagem = imagem.resize((nova_largura, max_height), Image.LANCZOS)
        logger.info(
            "Logo redimensionada de %sx%s para %sx%s", largura, altura, nova_largura, max_height
        )

    buffer = BytesIO()
    imagem.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)

    # Nome único a cada envio. Com um nome fixo (`logo.png`) o navegador
    # serviria a logo anterior do cache mesmo depois da troca — o arquivo muda,
    # a URL não.
    nome = f"logo-{uuid.uuid4().hex[:12]}.png"
    return SimpleUploadedFile(nome, buffer.read(), content_type="image/png")


def _trim_transparent_margin(imagem):
    """Remove a moldura transparente ao redor do desenho.

    Editores costumam exportar a logo centralizada em uma tela maior. Como a
    logo é desenhada com **altura fixa**, essa margem vira espaço vazio dentro
    da caixa e o desenho aparece menor do que deveria — uma logo 768x320 cujo
    traço ocupa 242 px de altura renderiza a 75% do tamanho disponível.

    Aparar aqui, uma vez, faz o desenho ocupar toda a altura em qualquer lugar
    do app, sem o proprietário precisar reexportar a arte.
    """
    if imagem.mode not in ("RGBA", "LA"):
        return imagem

    canal_alfa = imagem.getchannel("A")
    caixa = canal_alfa.getbbox()

    # `getbbox()` devolve None quando a imagem é inteiramente transparente:
    # nesse caso não há o que aparar (e recortar deixaria a imagem vazia).
    if caixa is None or caixa == (0, 0, imagem.width, imagem.height):
        return imagem

    logger.info("Margem transparente aparada: %s -> %s", imagem.size, caixa)
    return imagem.crop(caixa)
