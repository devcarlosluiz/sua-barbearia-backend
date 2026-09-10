"""Configuração da aplicação Celery da Sua Barbearia."""

from __future__ import annotations

import os

from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("suabarbearia")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@setup_logging.connect
def configure_logging(*args, **kwargs) -> None:  # pragma: no cover
    """Usa o dictConfig do Django também para os workers."""
    import logging.config

    from django.conf import settings

    logging.config.dictConfig(settings.LOGGING)


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> str:  # pragma: no cover
    return f"request: {self.request!r}"
