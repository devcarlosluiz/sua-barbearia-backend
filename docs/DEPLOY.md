# Deploy na VM — clmlabs.com.br

Ambiente de teste público: VM `188.220.168.196`, domínio `clmlabs.com.br`.

O que sobe: API em `/api/v1/`, admin do Django em `/admin/`, Swagger em
`/api/docs/`. O app Flutter vive em outro repositório e não é publicado por
aqui.

> **Por que a raiz do domínio e não `clmlabs.com.br/suabarbearia`.** Servir
> Django sob um subpath exige `FORCE_SCRIPT_NAME`, prefixar
> `STATIC_URL`/`MEDIA_URL`, ajustar `SESSION_COOKIE_PATH`/`CSRF_COOKIE_PATH` e
> reescrever caminhos no nginx — e qualquer URL absoluta gerada pelo Django
> (redirect de login do admin, links do Swagger) vira candidata a quebrar.
> Servir na raiz não custa linha de código nenhuma. Se o subpath virar
> requisito, dá para fazer depois, mas é outro trabalho.

---

## 1. DNS

O domínio é definido em **um único lugar**, a variável `DOMAIN` do `.env`. Ela
alimenta o `server_name` e o caminho do certificado no nginx (via `envsubst`
sobre o `docker/nginx/prod.conf.template`) e também o
`scripts/init_letsencrypt.sh`. Para trocar de domínio depois, mude o `DOMAIN`
e as três variáveis do Django logo abaixo dele — nenhum arquivo de
configuração do nginx precisa ser editado.

O apex `clmlabs.com.br` já aponta para a VM, então não há registro a criar.
Confirme antes de seguir:

```bash
dig +short clmlabs.com.br     # precisa devolver 188.220.168.196
```

> **Se um dia usar um subdomínio novo, crie o registro A ANTES de rodar o
> certbot.** Consultar um nome que ainda não existe faz os resolvers públicos
> guardarem o "não existe" por até uma hora — é o campo `minimum` do SOA da
> zona. Nesse intervalo o Let's Encrypt continua vendo NXDOMAIN mesmo com o
> registro já publicado, e cada tentativa queima uma das 5 validações por hora.
> O apex não tem esse problema porque sempre resolveu.

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
# SECRET_KEY (base64 não gera "$", veja abaixo por que isso importa)
openssl rand -base64 48 | tr -d '\n=' ; echo

# DATABASE_PASSWORD e SEED_DEFAULT_PASSWORD
openssl rand -base64 24 | tr -d '\n=' ; echo
```

Um `SECRET_KEY` começando com `$` é uma armadilha real: o `django-environ`
interpretaria o valor como referência a outra variável, não a encontraria, e
cairia no default inseguro do código — assinando todos os JWT com uma string
pública, sem erro nem aviso. Por isso geramos em base64, que não produz `$`.

> **Não use `docker compose run backend ...` aqui.** Sem os dois `-f`, o
> Compose usa o estágio `development` da imagem — e o `.env` de produção define
> `DJANGO_SETTINGS_MODULE=config.settings.prod`, que exige `whitenoise` e
> `gunicorn`. Esses dois só estão no `requirements/prod.txt`, então o comando
> morre com `ModuleNotFoundError: No module named 'whitenoise'`. Depois que o
> stack estiver de pé, `$CP exec backend ...` é seguro: usa o container que já
> está rodando, com a imagem de produção.

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
  "rm -rf /etc/letsencrypt/live/clmlabs.com.br \
          /etc/letsencrypt/archive/clmlabs.com.br \
          /etc/letsencrypt/renewal/clmlabs.com.br.conf"
```

Renovação é automática: o serviço `certbot` tenta a cada 12h (o Let's Encrypt
só renova faltando menos de 30 dias) e o nginx recarrega a cada 6h.

## 5. Subir

A rede compartilhada com o stack do app precisa existir antes. Crie uma vez:

```bash
docker network create suabarbearia_edge
```

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
curl -I https://clmlabs.com.br/health/  # 200
curl -I http://clmlabs.com.br/health/   # 301 para https
```

No navegador:

- https://clmlabs.com.br/ — redireciona para o Swagger
- https://clmlabs.com.br/api/docs/ — Swagger
- https://clmlabs.com.br/admin/ — admin
- https://clmlabs.com.br/health/ — status de banco e cache

Enquanto o app Flutter não estiver publicado, a raiz redireciona para o
Swagger. Depois do passo 7 ela passa a servir o app.

## 7. App Flutter (repositório `sua-barbearia-frontend`)

O app e a API ficam no **mesmo domínio**, com um único certificado. O nginx
deste repositório é a porta de entrada e reparte por caminho:

| Caminho | Destino |
|---|---|
| `/api/`, `/admin/`, `/health/`, `/static/`, `/media/` | Django |
| qualquer outro | container do app (`frontend`) |

Como app e API compartilham a origem, o navegador não faz requisição
cross-origin — o CORS deixa de participar.

No outro repositório, na mesma VM:

```bash
git clone <url-do-frontend> sua-barbearia-frontend
cd sua-barbearia-frontend

cp .env.prod.example .env     # API_BASE_URL precisa bater com o DOMAIN daqui
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

O build do Flutter demora alguns minutos. A `API_BASE_URL` é gravada **na
compilação**: trocá-la exige `--build`, reiniciar o container não adianta.

Os dois stacks continuam independentes — sobem, caem e são reconstruídos
separadamente. A ligação é só a rede `suabarbearia_edge`, onde o app se
apresenta com o apelido `frontend`.

> O nginx resolve o endereço do app **por requisição**, não na inicialização.
> Isso é deliberado: um `upstream` comum faria o nginx abortar com "host not
> found in upstream" enquanto o container do app não existisse, derrubando
> junto a API, o admin e o desafio do ACME. Do jeito atual, sem o app no ar a
> raiz apenas redireciona para o Swagger.

## 8. Depois que estabilizar

Ligue o HSTS no `.env` e reinicie o backend:

```
SECURE_HSTS_SECONDS=31536000
```

Deixamos em `0` no início de propósito: o HSTS faz o navegador **guardar por um
ano** a regra "só acesso este host por HTTPS". Se o certificado quebrar depois
disso, não há acesso em HTTP nem limpando o cache comum.

---

## Problemas comuns

**Antes de tudo: leia o log, não o resumo do `up`.** Com `restart: always`, um
container que sai com erro é reiniciado em laço e o Compose reporta
`is unhealthy` em vez de `exited (1)`. A mensagem esconde a causa:

```bash
$CP logs backend | tail -40
```

### `backend` reinicia em laço depois de um `up` de desenvolvimento na VM

Quase sempre é **volume herdado do stack errado**. Rodar `docker compose up`
sem os `-f` uma vez na VM é suficiente para deixar o ambiente quebrado, e o
`up` de produção seguinte não conserta sozinho: volumes existentes não são
recriados.

Duas formas de estragar, as duas com o mesmo conserto:

**1. Permissão em `staticfiles`/`media`.** A imagem de desenvolvimento roda
como `root` e cria esses diretórios com dono `root`. A de produção roda como o
usuário sem privilégios `suabarbearia`. Se os volumes foram criados pelo stack
de desenvolvimento, eles vêm com dono `root` e o `collectstatic` de produção
morre com `Permission denied`.

**2. Senha do Postgres fora de sincronia.** `POSTGRES_DB`, `POSTGRES_USER` e
`POSTGRES_PASSWORD` só têm efeito na **primeira** vez que o volume é criado. Se
você subiu antes de preencher o `.env` e depois trocou a senha, o banco continua
com a antiga e o Django falha com `FATAL: password authentication failed`. O
`postgres` ainda assim aparece como `Healthy`, porque o `pg_isready` só verifica
se o servidor aceita conexões — não autentica.

Como o ambiente é novo e não há dados a preservar:

```bash
$CP down -v      # -v descarta banco, media e staticfiles
$CP up --build -d
```

Num ambiente com dados, para o caso 2, troque a senha no servidor em vez de
recriar:

```bash
$CP exec postgres psql -U <usuario> -c "ALTER USER <usuario> WITH PASSWORD '<nova>';"
```

**Nada responde na porta 80/443.** Confira com `$CP ps` se o
`suabarbearia_nginx` está de pé. Se não estiver, veja `$CP logs nginx`: o erro
mais comum é o nginx não achar o certificado (rode o passo 4).

**Subi com `docker compose up` sem os `-f`.** Isso levantou o stack de
desenvolvimento, com 5432 e 6379 publicados. Derrube e suba direito — repare
que o `down` também precisa dos `-f`:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down --remove-orphans
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**`docker compose down` reclama `Network ... Resource is still in use`.** Foi
um `down` sem os `-f`. O projeto de desenvolvimento não conhece os serviços
`nginx` e `certbot`, que existem só no arquivo de produção: eles sobram
rodando e seguram a rede. Use sempre `$CP down` (com os dois `-f`); o
`--remove-orphans` limpa containers de uma composição anterior.

**`celery` e `celery-beat` não sobem.** Eles esperam o `backend` ficar
*healthy*, e o healthcheck chama `http://localhost:8000/health/`. Se
`ALLOWED_HOSTS` não contiver `localhost`, o Django devolve 400 DisallowedHost,
o healthcheck nunca passa e os dois ficam presos em `Waiting`. Veja com
`docker inspect --format '{{json .State.Health}}' suabarbearia_backend`.

**Loop de redirecionamento no navegador.** O nginx precisa repassar
`X-Forwarded-Proto` (o `prod.conf.template` já faz). Sem esse header o Django não
enxerga o HTTPS e redireciona para sempre.

**CSRF verification failed no login do admin.** Falta o domínio **com
esquema** em `CSRF_TRUSTED_ORIGINS`: `https://clmlabs.com.br`.

**Certbot falha com "Timeout during connect".** O desafio HTTP não chegou:
DNS ainda não propagou, ou a porta 80 está fechada no firewall do provedor.

**Certbot falha com "NXDOMAIN looking up A for ...".** O registro DNS não
existe. O script agora barra esse caso no passo 0, antes de mexer em qualquer
coisa — se você viu essa mensagem vinda do próprio certbot, está com uma versão
antiga do script.

Atenção à cota: o Let's Encrypt permite **5 validações falhas por hostname por
hora** no ambiente real. Por isso o ensaio com `STAGING=1` vem primeiro — o
ambiente de teste tem limites muito maiores. Se você estourar, a espera é de
uma hora.

**Logs:**

```bash
$CP logs -f backend
$CP logs -f nginx
$CP logs --tail=100 celery-beat
```
