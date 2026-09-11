"""O schema do OpenAPI precisa continuar sendo gerável.

Nenhuma rota de uso normal passa por aqui, então uma view que quebre apenas a
geração do schema fica invisível até alguém abrir o Swagger — em produção, de
preferência. Foi o que aconteceu: a `BrandingView` lia `self.request.method`
dentro de `get_authenticators()`, que o DRF chama antes de `self.request`
existir, e `/api/schema/` passou a responder 500 sem que nada mais falhasse.

O custo destes dois testes é baixo e eles cobrem TODAS as views do projeto de
uma vez: a geração percorre o roteador inteiro.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def test_schema_e_gerado_sem_erro(api: APIClient):
    response = api.get("/api/schema/")

    assert response.status_code == 200, response.content
    # Um corpo vazio passaria no status: confirmamos que saiu OpenAPI de fato.
    assert b"openapi" in response.content


def test_swagger_carrega(api: APIClient):
    assert api.get("/api/docs/").status_code == 200
