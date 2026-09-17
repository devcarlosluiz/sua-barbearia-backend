"""
Configurações base do projeto Sua Barbearia.

Todos os valores sensíveis são lidos de variáveis de ambiente (django-environ).
Nunca coloque SECRET_KEY, senhas ou chaves de API diretamente neste arquivo.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# O .env fica na raiz deste repositório, ao lado do manage.py.
#
# Antes havia um fallback para `BASE_DIR.parent`, herdado de quando backend e
# app viviam no mesmo monorepo e dividiam um .env na raiz. Com os repositórios
# separados esse caminho passou a ser a pasta que *contém* o repositório —
# qualquer .env de um projeto vizinho seria carregado aqui. Por isso saiu.
env_file = BASE_DIR / ".env"
if env_file.exists():
    env.read_env(str(env_file))

# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# ---------------------------------------------------------------------------
# Aplicações
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "django_celery_beat",
    "django_celery_results",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.branches",
    "apps.barbers",
    "apps.clients",
    "apps.services",
    "apps.appointments",
    "apps.payments",
    "apps.finance",
    "apps.products",
    "apps.inventory",
    "apps.loyalty",
    "apps.plans",
    "apps.reviews",
    "apps.notifications",
    "apps.reports",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "apps.core.middleware.RequestIDMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.CurrentUserMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DATABASE_NAME", default="suabarbearia"),
        "USER": env("DATABASE_USER", default="suabarbearia"),
        "PASSWORD": env("DATABASE_PASSWORD", default="suabarbearia"),
        "HOST": env("DATABASE_HOST", default="localhost"),
        "PORT": env("DATABASE_PORT", default="5432"),
        "CONN_MAX_AGE": env.int("DATABASE_CONN_MAX_AGE", default=60),
        "ATOMIC_REQUESTS": False,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internacionalização
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / "locale"]

# ---------------------------------------------------------------------------
# Arquivos estáticos e mídia
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Cache (Redis)
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "IGNORE_EXCEPTIONS": True,
        },
        "KEY_PREFIX": "suabarbearia",
    }
}

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("apps.core.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_RENDERER_CLASSES": ("apps.core.renderers.EnvelopeJSONRenderer",),
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="60/min"),
        "user": env("THROTTLE_USER", default="1000/hour"),
        "login": env("THROTTLE_LOGIN", default="10/min"),
        "password_reset": env("THROTTLE_PASSWORD_RESET", default="5/hour"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_LIFETIME", default=60)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_LIFETIME", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default=SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "apps.accounts.serializers.SuaBarbeariaTokenObtainPairSerializer",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Sua Barbearia API",
    "DESCRIPTION": (
        "API REST da plataforma Sua Barbearia — gestão multi-filial de "
        "agendamentos, atendimentos, financeiro, estoque, fidelidade e relatórios."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "SORT_OPERATIONS": False,
    "ENUM_NAME_OVERRIDES": {
        "AppointmentStatusEnum": "apps.appointments.models.AppointmentStatus.choices",
        "PaymentStatusEnum": "apps.payments.models.PaymentStatus.choices",
        "PaymentMethodEnum": "apps.payments.models.PaymentMethod.choices",
        "SaleStatusEnum": "apps.inventory.models.SaleStatus.choices",
        "CommissionStatusEnum": "apps.finance.models.CommissionStatus.choices",
        "TransactionTypeEnum": "apps.finance.models.TransactionType.choices",
        "TransactionCategoryEnum": "apps.finance.models.TransactionCategory.choices",
        "StockMovementTypeEnum": "apps.inventory.models.StockMovementType.choices",
        "LoyaltyTransactionTypeEnum": "apps.loyalty.models.LoyaltyTransactionType.choices",
        "LoyaltyRewardTypeEnum": "apps.loyalty.models.LoyaltyRewardType.choices",
        "NotificationTypeEnum": "apps.notifications.models.NotificationType.choices",
        "UserRoleEnum": "apps.accounts.models.UserRole.choices",
        "WeekdayEnum": "apps.branches.models.Weekday.choices",
        "TimeOffTypeEnum": "apps.barbers.models.TimeOffType.choices",
        "CancelledByEnum": "apps.appointments.models.CancelledBy.choices",
        "DevicePlatformEnum": "apps.notifications.models.DevicePlatform.choices",
        "BrazilianStateEnum": "apps.branches.models.BrazilianState.choices",
    },
}

# ---------------------------------------------------------------------------
# Entrar com o Google
# ---------------------------------------------------------------------------
# Client IDs aceitos no ID token (claim `aud`). São públicos — o segredo do
# OAuth não é usado aqui, porque quem fala com o Google é o app.
#
# Informe TODOS os client IDs da credencial: o Android, o iOS e o Web têm IDs
# diferentes, e o token só é aceito se o `aud` dele estiver nesta lista. Sem
# nenhum valor, `/auth/google/` responde 503 e o resto do login segue normal.
GOOGLE_OAUTH_CLIENT_IDS = env.list("GOOGLE_OAUTH_CLIENT_IDS", default=[])

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:8080"])
CORS_ALLOW_CREDENTIALS = True
CORS_EXPOSE_HEADERS = ["X-Request-ID"]

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="django-db")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CELERY_BEAT_SCHEDULE = {
    "enviar-lembretes-de-agendamento": {
        "task": "apps.notifications.tasks.send_appointment_reminders",
        "schedule": crontab(minute="*/15"),
    },
    "marcar-no-show": {
        "task": "apps.notifications.tasks.mark_no_show_appointments",
        "schedule": crontab(minute="*/30"),
    },
    "limpar-notificacoes-antigas": {
        "task": "apps.notifications.tasks.cleanup_old_notifications",
        "schedule": crontab(hour=4, minute=0),
    },
    # --- Assinaturas ---
    "renovar-faturas-pix": {
        "task": "plans.renew_pix_invoices",
        "schedule": crontab(hour=6, minute=0),
    },
    # Rede de segurança para webhook perdido: confirma o PIX pago mesmo se a
    # notificação do provedor não chegar.
    "sincronizar-faturas-de-assinatura": {
        "task": "plans.sync_subscription_payments",
        "schedule": crontab(minute="*/20"),
    },
    "suspender-assinaturas-em-atraso": {
        "task": "plans.suspend_overdue",
        "schedule": crontab(hour=5, minute=30),
    },
}

# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="nao-responda@suabarbearia.com.br")

# Servidor SMTP. Só entra em uso quando EMAIL_BACKEND aponta para o backend
# SMTP do Django — com o backend de console estes valores são ignorados.
#
# Para o Gmail: host smtp.gmail.com, porta 587 e TLS. A senha é uma "Senha de
# app" gerada na conta Google, nunca a senha de login: a senha normal é
# recusada, e uma senha de app pode ser revogada sozinha.
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
# Sem timeout, um SMTP que não responde trava o worker até o servidor desistir.
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)
FRONTEND_RESET_PASSWORD_URL = env(
    "FRONTEND_RESET_PASSWORD_URL", default="http://localhost:8080/reset-password"
)

# ---------------------------------------------------------------------------
# Regras de negócio (defaults globais; podem ser sobrescritos por filial)
# ---------------------------------------------------------------------------
SUA_BARBEARIA = {
    "COMPANY_NAME": "Sua Barbearia",
    "DEFAULT_SLOT_INTERVAL_MINUTES": 30,
    "DEFAULT_CANCELLATION_LIMIT_HOURS": 2,
    "DEFAULT_COMMISSION_PERCENTAGE": "40.00",
    "LOYALTY_POINTS_PER_CURRENCY_UNIT": "1.00",
    "LOYALTY_POINT_VALUE": "0.04",
    "MAX_ADVANCE_BOOKING_DAYS": 90,
    "REMINDER_HOURS_BEFORE": [24, 2],
}

SEED_DEFAULT_PASSWORD = env("SEED_DEFAULT_PASSWORD", default="SuaBarbearia@2026")

# ---------------------------------------------------------------------------
# Push notifications (integração futura com Firebase Cloud Messaging)
# ---------------------------------------------------------------------------
FIREBASE_PROJECT_ID = env("FIREBASE_PROJECT_ID", default="")
FIREBASE_CREDENTIALS_JSON = env("FIREBASE_CREDENTIALS_JSON", default="")

# ---------------------------------------------------------------------------
# Asaas (planos mensais)
# ---------------------------------------------------------------------------
# Todos os segredos vêm do ambiente. Sem `ASAAS_API_KEY` a assinatura online
# simplesmente não é oferecida — o restante do sistema continua funcionando,
# inclusive a confirmação manual de fatura no caixa.
ASAAS_API_KEY = env("ASAAS_API_KEY", default="")
# Produção: https://api.asaas.com/v3 — sandbox: https://sandbox.asaas.com/api/v3
ASAAS_BASE_URL = env("ASAAS_BASE_URL", default="https://api.asaas.com/v3")
# Token estático configurado ao cadastrar o webhook no painel do Asaas. Sem
# ele o endpoint recusa as chamadas em produção, para não aceitar confirmação
# de pagamento de qualquer origem.
ASAAS_WEBHOOK_TOKEN = env("ASAAS_WEBHOOK_TOKEN", default="")

SUBSCRIPTION_SETTINGS = {
    # Validade do QR do PIX, em minutos.
    "PIX_EXPIRATION_MINUTES": env.int("SUBSCRIPTION_PIX_EXPIRATION_MINUTES", default=60),
    # Dias antes do fim do ciclo em que a próxima fatura PIX é emitida.
    "PIX_RENEWAL_LEAD_DAYS": env.int("SUBSCRIPTION_PIX_RENEWAL_LEAD_DAYS", default=5),
    # Dias de tolerância após o vencimento antes de suspender o benefício.
    "GRACE_PERIOD_DAYS": env.int("SUBSCRIPTION_GRACE_PERIOD_DAYS", default=3),
    # Consulta ao provedor logo depois de emitir o QR, para o plano ativar
    # enquanto o cliente ainda está com o app aberto. O padrão cobre os 10
    # primeiros minutos; depois disso a varredura de 20 em 20 minutos assume.
    # `0` tentativas desliga a consulta e deixa tudo por conta do webhook.
    "PIX_POLL_INTERVAL_SECONDS": env.int("SUBSCRIPTION_PIX_POLL_INTERVAL_SECONDS", default=15),
    "PIX_POLL_ATTEMPTS": env.int("SUBSCRIPTION_PIX_POLL_ATTEMPTS", default=40),
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "apps.core.logging.RequestIDFilter"},
    },
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} [req:{request_id}] {message}",
            "style": "{",
        },
        "simple": {"format": "[{levelname}] {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django.db.backends": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "suabarbearia.application": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "suabarbearia.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "suabarbearia.api": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "suabarbearia.celery": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "suabarbearia.database": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
