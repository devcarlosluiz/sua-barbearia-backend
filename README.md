# Sua Barbearia — Backend

API REST da plataforma de gestão da rede Sua Barbearia: agendamento, atendimento,
financeiro, comissões, estoque, fidelidade, planos mensais, avaliações e
relatórios.

Django REST Framework · PostgreSQL · Redis · Celery.

```
Flutter (Android · iOS · Web)   ← repositório sua-barbearia-frontend
        │  HTTPS / REST / JSON  (JWT)
        ▼
Django REST Framework ── PostgreSQL
        ├── Redis (cache + broker)
        └── Celery + Beat (lembretes, notificações)
```

> **O app Flutter fica em outro repositório** (`sua-barbearia-frontend`), com o seu
> próprio `docker-compose`. Os dois stacks são independentes e não compartilham
> rede Docker: o app roda no navegador (ou no celular) e chama esta API em
> `http://localhost:8000`. O que autoriza essa chamada é o `CORS_ALLOWED_ORIGINS`.

## Sumário

- [Começando em 3 comandos](#começando-em-3-comandos)
- [Usuários de teste](#usuários-de-teste)
- [Comandos do dia a dia](#comandos-do-dia-a-dia)
- [Sem Docker](#sem-docker)
- [Celery](#celery)
- [Testes](#testes)
- [Estrutura](#estrutura)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Produção](#produção)
- [Documentação](#documentação)

---

## Começando em 3 comandos

Pré-requisitos: **Docker** e **Docker Compose**.

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec backend python manage.py seed_data
```

Pronto. Serviços disponíveis:

| Recurso | Endereço |
|---|---|
| API | http://localhost:8000/api/v1/ |
| Documentação (Swagger) | http://localhost:8000/api/docs/ |
| Documentação (ReDoc) | http://localhost:8000/api/redoc/ |
| Schema OpenAPI | http://localhost:8000/api/schema/ |
| Admin Django | http://localhost:8000/admin/ |
| Health check | http://localhost:8000/health/ |

Containers do stack: `suabarbearia_postgres`, `suabarbearia_redis`, `suabarbearia_backend`,
`suabarbearia_celery`, `suabarbearia_celery_beat` — e `suabarbearia_nginx` apenas no perfil
`full` (`docker compose --profile full up -d`, publicado em
http://localhost:8081).

Validando o fluxo real ponta a ponta (login → agendamento → atendimento →
pagamento → avaliação → dashboards):

```bash
python scripts/smoke_test.py http://localhost:8000
```

## Usuários de teste

Criados pelo `seed_data`. A senha vem de `SEED_DEFAULT_PASSWORD` no `.env`
(padrão de desenvolvimento: `SuaBarbearia@2026`).

| Papel | E-mail |
|---|---|
| OWNER | `owner@suabarbearia.com` |
| BARBER | `barber@suabarbearia.com` |
| CLIENT | `client@suabarbearia.com` |

O seed cria 3 filiais, 8 barbeiros, 20 clientes, 10 serviços, 10 produtos com
estoque, 50 agendamentos (com pagamentos, comissões, pontos e avaliações) e
lançamentos de despesa dos últimos dois meses.

---

## Comandos do dia a dia

```bash
docker compose up --build -d        # sobe backend, postgres, redis, celery e beat
docker compose logs -f backend
docker compose down
docker compose down -v              # descarta também o banco

docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py makemigrations
docker compose exec backend python manage.py createsuperuser
docker compose exec backend python manage.py seed_data          # popula dados
docker compose exec backend python manage.py seed_data --flush  # limpa e repopula
docker compose exec backend python manage.py spectacular --file schema.yml
```

## Sem Docker

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
export DJANGO_SETTINGS_MODULE=config.settings.dev
python manage.py migrate
python manage.py runserver
```

É preciso ter PostgreSQL e Redis rodando localmente e ajustar `DATABASE_HOST`
(`localhost`) e `REDIS_URL` no `.env` — os valores do exemplo apontam para os
nomes de serviço do Compose.

## Celery

```bash
docker compose exec backend celery -A config worker -l info
docker compose exec backend celery -A config beat -l info
```

Tarefas agendadas: lembretes de agendamento (24h e 2h antes), marcação
automática de no-show e limpeza de notificações antigas.

---

## Testes

```bash
docker compose exec backend pytest
docker compose exec backend pytest --cov=apps --cov-report=term-missing
docker compose exec backend pytest apps/appointments -v

docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
```

Cobre autenticação, permissões por papel, disponibilidade de horários,
conflitos de agendamento, cancelamento, reagendamento, conclusão de
atendimento, financeiro, comissões, estoque, vendas, fidelidade, planos
mensais (assinatura, cobrança, webhook e consumo de cota) e avaliações.

> O `Dockerfile` define `ENV DJANGO_SETTINGS_MODULE=config.settings.dev`, que
> tem precedência sobre o `pytest.ini`. Por isso o `pytest.ini` traz
> `--ds=config.settings.test` em `addopts`: sem isso os testes rodariam com o
> Celery real e qualquer `.delay()` travaria esperando o broker.

---

## Estrutura

```
sua-barbearia-backend/
├── config/                     # settings (base/dev/test/prod), urls, celery
├── apps/
│   ├── core/                   # base models, permissões, envelope, auditoria
│   ├── accounts/               # usuário customizado + JWT
│   ├── branches/               # filiais, funcionamento, feriados
│   ├── barbers/                # barbeiros, jornada, ausências
│   ├── clients/                # clientes
│   ├── services/               # catálogo de serviços
│   ├── appointments/           # agendamento, disponibilidade, atendimento
│   ├── payments/               # pagamentos (agnóstico de gateway)
│   ├── finance/                # lançamentos e comissões
│   ├── products/               # catálogo de produtos
│   ├── inventory/              # estoque, movimentações e vendas
│   ├── loyalty/                # pontos e recompensas
│   ├── reviews/                # avaliações
│   ├── notifications/          # notificações e push
│   └── reports/                # dashboards e relatórios
├── requirements/               # base.txt, dev.txt, prod.txt
├── scripts/smoke_test.py       # teste de fumaça da API
├── docker/nginx/nginx.conf     # proxy reverso (static/media)
├── docs/                       # ARCHITECTURE.md e API.md
├── Dockerfile                  # estágios development e production
├── docker-compose.yml
├── docker-compose.prod.yml
└── .env.example
```

---

## Variáveis de ambiente

Copie `.env.example` para `.env`. Nunca versione o `.env`.

| Variável | Descrição |
|---|---|
| `DEBUG` | `True` em desenvolvimento |
| `SECRET_KEY` | Chave do Django (troque em produção) |
| `DATABASE_*` | Conexão com o PostgreSQL |
| `REDIS_URL` | Cache |
| `CELERY_BROKER_URL` | Broker do Celery |
| `ALLOWED_HOSTS` | Hosts permitidos |
| `CORS_ALLOWED_ORIGINS` | Origens liberadas para o Flutter Web |
| `JWT_ACCESS_LIFETIME` | Validade do access token (minutos) |
| `JWT_REFRESH_LIFETIME` | Validade do refresh token (dias) |
| `EMAIL_*` | Envio de e-mail (dev usa o backend de console) |
| `MERCADO_PAGO_*` | Planos mensais (checkout e webhook) |
| `FIREBASE_PROJECT_ID` | Push notification (opcional) |
| `SENTRY_DSN` | Observabilidade (opcional) |
| `SEED_DEFAULT_PASSWORD` | Senha dos usuários do seed (dev) |

Ao mudar a porta ou o host em que o Flutter Web é servido, acrescente a nova
origem em `CORS_ALLOWED_ORIGINS` e reinicie o backend.

---

## Produção

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
docker compose exec backend python manage.py collectstatic --noinput
```

O `docker-compose.prod.yml` usa o estágio `production` do Dockerfile
(Gunicorn + WhiteNoise), ativa `config.settings.prod` (HSTS, cookies seguros,
SSL redirect) e sobe o Nginx como proxy reverso servindo `static/` e `media/`
nas portas 80/443.

Checklist antes de publicar:

- [ ] `DEBUG=False` e `SECRET_KEY` forte
- [ ] `ALLOWED_HOSTS` e `CORS_ALLOWED_ORIGINS` com os domínios reais
- [ ] Certificado TLS em `docker/nginx/certs/` e `SECURE_SSL_REDIRECT=True`
- [ ] Backup automatizado do PostgreSQL
- [ ] `SENTRY_DSN` configurado
- [ ] Workers do Celery e o Beat rodando como serviços gerenciados

---

## Documentação

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — arquitetura, ERD, regras de
  negócio críticas e plano de fases (cobre backend e app).
- [`docs/API.md`](docs/API.md) — endpoints, envelope de resposta e códigos de erro.
- Swagger/ReDoc em `/api/docs/` e `/api/redoc/` com o servidor rodando.
