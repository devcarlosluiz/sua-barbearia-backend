#!/usr/bin/env python
"""Utilitário de linha de comando do Django para o backend Sua Barbearia."""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Não foi possível importar o Django. Verifique se ele está instalado "
            "e se o ambiente virtual está ativado."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
