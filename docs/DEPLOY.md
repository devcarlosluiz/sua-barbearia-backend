# Deploy na VM — teste.clmlabs.com.br

Ambiente de teste público: VM `188.220.168.196`, domínio `clmlabs.com.br`.

O que sobe: API em `/api/v1/`, admin do Django em `/admin/`, Swagger em
`/api/docs/`. O app Flutter vive em outro repositório e não é publicado por
aqui.

> **Por que subdomínio e não `clmlabs.com.br/suabarbearia`.** Servir Django sob
> um subpath exige `FORCE_SCRIPT_NAME`, prefixar `STATIC_URL`/`MEDIA_URL`,
> ajustar `SESSION_COOKIE_PATH`/`CSRF_COOKIE_PATH` e reescrever caminhos no
> nginx — e qualquer URL absoluta gerada pelo Django (redirect de login do
> admin, links do Swagger) vira candidata a quebrar. O subdomínio custa um
> registro DNS e nenhuma linha de código. Se o subpath for requisito, dá para
> fazer depois, mas é outro trabalho.

---

## 1. DNS

No painel do `clmlabs.com.br`, crie:

| Tipo | Nome | Valor |
|---|---|---|
| A | `teste` | `188.220.168.196` |

Confirme a propagação antes de seguir — o Let's Encrypt vai consultar este
registro e falha se ele ainda não resolver:

```bash
dig +short teste.clmlabs.com.br     # precisa devolver 188.220.168.196
```

## 2. Preparar a VM

```bash
# Docker + plugin do Compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER" && newgrp docker

# Portas 80 e 443 abertas. Se usa ufw:
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
```

Se o provedor da VM tiver firewall próprio (security group no painel), libere
80 e 443 lá também. **Não** libere a 5432 nem a 6379: em produção o compose
não publica essas portas no host, e o backend fala com o banco e o Redis pela
rede interna do Docker.

Confirme que nada está ocupando as portas — um Apache ou nginx do sistema
impede o container de subir:

```bash
sudo ss -tlnp '( sport = :80 or sport = :443 )'
```

## 3. Código e variáveis

```bash
git clone <url-do-repositorio> sua-barbearia-backend
cd sua-barbearia-backend

cp .env.prod.example .env
```

Edite o `.env` e preencha os três campos marcados com `TROQUE`:

```bash
# SECRET_KEY — gere e confira que não começa com "$"
docker compose run --rm backend python -c \
  "from django.core.management.utils import get_random_secret_key as g; k=g(); print(k if not k.startswith('\$') else g())"

# DATABASE_PASSWORD e SEED_DEFAULT_PASSWORD
openssl rand -base64 24
```

O `SECRET_KEY` começando com `$` é uma armadilha real: o `django-environ`
interpretaria o valor como referência a outra variável, não a encontraria, e
cairia no default inseguro do código — assinando todos os JWT com uma string
pública, sem erro nem aviso. O comando acima já regera nesse caso.

## 4. Certificado TLS

Uma vez só. O script resolve o ovo-e-galinha (o nginx não sobe sem um `.pem`,
e o Let's Encrypt não emite sem um nginx respondendo): ele sobe com um
certificado auto-assinado descartável e troca pelo real.

```bash
chmod +x scripts/init_letsencrypt.sh

# Ensaio, sem gastar a cota semanal do Let's Encrypt:
STAGING=1 LETSENCRYPT_EMAIL=voce@exemplo.com ./scripts/init_letsencrypt.sh

# Deu certo? Rode de novo para valer:
LETSENCRYPT_EMAIL=voce@exemplo.com ./scripts/init_letsencrypt.sh
```

Entre um e outro, limpe o certificado de staging:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm --entrypoint sh certbot -c \
  "rm -rf /etc/letsencrypt/live/teste.clmlabs.com.br \
          /etc/letsencrypt/archive/teste.clmlabs.com.br \
          /etc/letsencrypt/renewal/teste.clmlabs.com.br.conf"
```

Renovação é automática: o serviço `certbot` tenta a cada 12h (o Let's Encrypt
só renova faltando menos de 30 dias) e o nginx recarrega a cada 6h.

## 5. Subir

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

Os dois `-f` **não são opcionais**. Sem eles o Compose carrega também o
`docker-compose.override.yml`, que é o arquivo de desenvolvimento: o Postgres
passa a publicar a 5432 e o Redis a 6379 no host — numa VM pública, isso deixa
o Redis, que não pede autenticação, aberto para a internet. O override também
monta o código do host por cima da imagem.

Na dúvida, confirme antes de subir:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps --format \
  "table {{.Service}}\t{{.Ports}}"
```

Só o `nginx` pode aparecer com portas publicadas (80 e 443).

Migrations rodam sozinhas no boot do `backend`. Falta criar o acesso:

```bash
CP="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

$CP exec backend python manage.py createsuperuser

# Opcional: dados de demonstração (3 filiais, barbeiros, agendamentos...)
$CP exec backend python manage.py seed_data
```

## 6. Conferir

```bash
$CP ps                                        # os 6 containers de pé
curl -I https://teste.clmlabs.com.br/health/  # 200
curl -I http://teste.clmlabs.com.br/health/   # 301 para https
```

No navegador:

- https://teste.clmlabs.com.br/api/docs/ — Swagger
- https://teste.clmlabs.com.br/admin/ — admin
- https://teste.clmlabs.com.br/health/ — status de banco e cache

## 7. Depois que estabilizar

Ligue o HSTS no `.env` e reinicie o backend:

```
SECURE_HSTS_SECONDS=31536000
```

Deixamos em `0` no início de propósito: o HSTS faz o navegador **guardar por um
ano** a regra "só acesso este host por HTTPS". Se o certificado quebrar depois
disso, não há acesso em HTTP nem limpando o cache comum.

---

## Problemas comuns

**Nada responde na porta 80/443.** Confira com `$CP ps` se o
`suabarbearia_nginx` está de pé. Se não estiver, veja `$CP logs nginx`: o erro
mais comum é o nginx não achar o certificado (rode o passo 4).

**Subi com `docker compose up` sem os `-f`.** Isso levantou o stack de
desenvolvimento, com 5432 e 6379 publicados. Derrube e suba direito:

```bash
docker compose down
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**`celery` e `celery-beat` não sobem.** Eles esperam o `backend` ficar
*healthy*, e o healthcheck chama `http://localhost:8000/health/`. Se
`ALLOWED_HOSTS` não contiver `localhost`, o Django devolve 400 DisallowedHost,
o healthcheck nunca passa e os dois ficam presos em `Waiting`. Veja com
`docker inspect --format '{{json .State.Health}}' suabarbearia_backend`.

**Loop de redirecionamento no navegador.** O nginx precisa repassar
`X-Forwarded-Proto` (o `prod.conf` já faz). Sem esse header o Django não
enxerga o HTTPS e redireciona para sempre.

**CSRF verification failed no login do admin.** Falta o domínio **com
esquema** em `CSRF_TRUSTED_ORIGINS`: `https://teste.clmlabs.com.br`.

**Certbot falha com "Timeout during connect".** O desafio HTTP não chegou:
DNS ainda não propagou, ou a porta 80 está fechada no firewall do provedor.

**Logs:**

```bash
$CP logs -f backend
$CP logs -f nginx
$CP logs --tail=100 celery-beat
```
