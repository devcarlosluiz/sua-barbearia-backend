"""Configurações usadas pela suíte de testes."""

from .base import *

DEBUG = False
ALLOWED_HOSTS = ["*"]

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Com o Celery em modo eager, `apply_async` executa na hora e ignora o
# `countdown`: a consulta da fatura recém-emitida se reagendaria dezenas de
# vezes dentro de cada `subscribe()`. Desligada por padrão; o teste que exercita
# a rotina liga o número de tentativas que precisa.
SUBSCRIPTION_SETTINGS = {**SUBSCRIPTION_SETTINGS, "PIX_POLL_ATTEMPTS": 0}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    "anon": None,
    "user": None,
    "login": None,
    "password_reset": None,
}

MEDIA_ROOT = BASE_DIR / "test-media"

GOOGLE_OAUTH_CLIENT_IDS = ["test-client-id.apps.googleusercontent.com"]
