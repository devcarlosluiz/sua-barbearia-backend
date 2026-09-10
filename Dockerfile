# syntax=docker/dockerfile:1

# ---------- Base ----------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        gettext \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ /app/requirements/

# ---------- Development ----------
FROM base AS development
ENV DJANGO_SETTINGS_MODULE=config.settings.dev
RUN pip install -r requirements/dev.txt
COPY . /app
RUN mkdir -p /app/media /app/staticfiles
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---------- Production ----------
FROM base AS production
ENV DJANGO_SETTINGS_MODULE=config.settings.prod
RUN pip install -r requirements/prod.txt
COPY . /app
RUN mkdir -p /app/media /app/staticfiles \
    && addgroup --system suabarbearia && adduser --system --ingroup suabarbearia suabarbearia \
    && chown -R suabarbearia:suabarbearia /app
USER suabarbearia
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60"]
