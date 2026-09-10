"""Configurações de desenvolvimento."""

from .base import *
from .base import INSTALLED_APPS, REST_FRAMEWORK, env

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [*INSTALLED_APPS, "django_extensions"]

# Em desenvolvimento também expomos o browsable API do DRF.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (
    "apps.core.renderers.EnvelopeJSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
)

CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=True)
