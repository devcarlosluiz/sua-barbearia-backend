#!/usr/bin/env bash
#
# Emissão inicial do certificado TLS. Roda UMA vez, na VM, antes do primeiro
# `up` completo. Depois disso o serviço `certbot` do compose cuida da renovação.
#
#   LETSENCRYPT_EMAIL=voce@exemplo.com ./scripts/init_letsencrypt.sh
#
# Variáveis aceitas:
#   LETSENCRYPT_EMAIL  (obrigatória) recebe os avisos de expiração
#   DOMAIN             lido do .env; pode ser sobrescrito aqui
#   STAGING=1          ensaia contra o ambiente de teste do Let's Encrypt
#   SKIP_DNS_CHECK=1   pula a verificação de DNS (raramente necessário)
#
# O problema que este script resolve: o nginx não sobe se o arquivo de
# certificado não existir, e o Let's Encrypt não emite o certificado sem um
# nginx no ar respondendo ao desafio HTTP. A saída é subir com um certificado
# auto-assinado descartável e trocá-lo pelo real em seguida.
set -euo pipefail

# O domínio vem do .env, o mesmo que o nginx usa — assim não há dois lugares
# para manter em sincronia. Pode ser sobrescrito na linha de comando.
if [ -z "${DOMAIN:-}" ] && [ -f .env ]; then
    DOMAIN=$(sed -n 's/^[[:space:]]*DOMAIN[[:space:]]*=[[:space:]]*//p' .env \
             | head -1 | tr -d '"'\''' | tr -d '\r')
fi
DOMAIN="${DOMAIN:?defina DOMAIN no .env (ex.: DOMAIN=clmlabs.com.br) ou na linha de comando}"
# Sem apóstrofo nesta mensagem: dentro de ${VAR:?...} ele abre uma citação e
# quebra o parser do bash.
EMAIL="${LETSENCRYPT_EMAIL:?defina LETSENCRYPT_EMAIL. Este endereço recebe os avisos de expiração do certificado}"
STAGING="${STAGING:-0}"

# Os `-f` explícitos são obrigatórios: sem eles o Compose carregaria o
# docker-compose.override.yml e este script mexeria no stack de desenvolvimento.
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
LIVE="/etc/letsencrypt/live/$DOMAIN"

# Recria o certificado auto-assinado descartável. É só para o nginx ter um
# arquivo válido para carregar; o navegador vai recusá-lo.
criar_temporario() {
    "${COMPOSE[@]}" run --rm --entrypoint sh certbot -c "
        mkdir -p '$LIVE' &&
        openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
            -keyout '$LIVE/privkey.pem' -out '$LIVE/fullchain.pem' \
            -subj '/CN=$DOMAIN' 2>/dev/null
    " >/dev/null
}

# ---------------------------------------------------------------------------
# 0. DNS antes de tudo.
#
# Sem este passo o script apagaria o certificado temporário (passo 3) e só
# então descobriria, no passo 4, que o domínio não resolve — deixando o nginx
# sem certificado algum. Pior: cada tentativa fracassada consome a cota de
# validações do Let's Encrypt (5 por hora por hostname no ambiente real).
# ---------------------------------------------------------------------------

# Resolve $1 para IPv4, imprimindo um IP por linha. Tenta as ferramentas na
# ordem de disponibilidade — nenhuma delas está garantida numa VM enxuta.
# Devolve 2 quando NENHUMA existe, para não confundir "não resolve" com
# "não sei checar".
resolver_ipv4() {
    local nome="$1" saida rc

    # Não basta o `command -v` achar o binário: ele pode existir e não
    # funcionar (no Git Bash do Windows, `python3` é um atalho da Microsoft
    # Store que sai com 49). Por isso cada ferramenta é executada e só é
    # aceita se o código de saída disser que ela de fato rodou.
    if command -v getent >/dev/null 2>&1; then
        saida=$(getent ahostsv4 "$nome" 2>/dev/null); rc=$?
        # getent: 0 = encontrou, 2 = não encontrou. Os dois valem como
        # "a ferramenta funcionou e deu a resposta".
        if [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]; then
            printf '%s\n' "$saida" | awk 'NF {print $1}' | sort -u
            return 0
        fi
    fi

    if command -v dig >/dev/null 2>&1; then
        saida=$(dig +short A "$nome" 2>/dev/null); rc=$?
        if [ "$rc" -eq 0 ]; then
            printf '%s\n' "$saida" | grep -E '^[0-9.]+$' || true
            return 0
        fi
    fi

    if command -v nslookup >/dev/null 2>&1; then
        saida=$(nslookup "$nome" 2>/dev/null) || true
        if [ -n "$saida" ]; then
            # A primeira linha "Address" é o servidor DNS consultado, não a
            # resposta; por isso só imprimimos a partir da segunda.
            printf '%s\n' "$saida" | awk '/^Address/ {n++; if (n > 1) print $2}' \
                | grep -E '^[0-9.]+$' || true
            return 0
        fi
    fi

    return 2   # nenhuma ferramenta utilizável
}

if [ "${SKIP_DNS_CHECK:-0}" = "0" ]; then
    echo ">> 0/5 Conferindo se $DOMAIN resolve"
    ips=$(resolver_ipv4 "$DOMAIN") || checagem=indisponivel

    if [ "${checagem:-ok}" = "indisponivel" ]; then
        echo "   AVISO: nenhuma ferramenta de DNS encontrada; seguindo sem checar." >&2
    elif [ -z "$ips" ]; then
        cat >&2 <<EOF

ERRO: $DOMAIN não resolve (NXDOMAIN).

O Let's Encrypt consulta o DNS público para validar o domínio, então nem
adianta tentar. Crie o registro no painel do seu provedor:

    Tipo  A
    Nome  ${DOMAIN%%.*}
    Valor <IP público desta VM>

Depois confirme e rode este script de novo:

    dig +short $DOMAIN

Se o registro acabou de ser criado, espere alguns minutos: o NXDOMAIN anterior
fica em cache negativo nos resolvers.
EOF
        exit 1
    else
        echo "   resolve para: $(echo "$ips" | tr '\n' ' ')"
        echo "   confira que é o IP público desta VM."
    fi
fi

# O ambiente de staging tem limite de emissão muito maior. Use STAGING=1 para
# ensaiar: o certificado sai inválido para o navegador, mas prova que o
# desafio funciona sem queimar a cota semanal do ambiente real.
staging_flag=()
if [ "$STAGING" != "0" ]; then
    staging_flag=(--staging)
    echo ">> STAGING ligado: o certificado NÃO será confiável no navegador."
fi

echo ">> 1/5 Certificado temporário para o nginx conseguir subir"
criar_temporario

echo ">> 2/5 Subindo o nginx"
"${COMPOSE[@]}" up -d nginx

echo ">> 3/5 Descartando o temporário"
"${COMPOSE[@]}" run --rm --entrypoint sh certbot -c "
    rm -rf '/etc/letsencrypt/live/$DOMAIN' \
           '/etc/letsencrypt/archive/$DOMAIN' \
           '/etc/letsencrypt/renewal/$DOMAIN.conf'
"

echo ">> 4/5 Pedindo o certificado real ao Let's Encrypt"
if ! "${COMPOSE[@]}" run --rm --entrypoint certbot certbot \
    certonly --webroot -w /var/www/certbot \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --non-interactive \
    "${staging_flag[@]}"
then
    # Sem isto o nginx ficaria sem nenhum certificado e não subiria no próximo
    # restart — um segundo problema por cima do que já falhou.
    echo >&2
    echo ">> A emissão falhou. Recriando o certificado temporário para o nginx" >&2
    echo ">> continuar subindo. Corrija a causa acima e rode o script de novo." >&2
    criar_temporario
    "${COMPOSE[@]}" exec nginx nginx -s reload 2>/dev/null || true
    exit 1
fi

echo ">> 5/5 Recarregando o nginx com o certificado válido"
"${COMPOSE[@]}" exec nginx nginx -s reload

echo
echo "Pronto. Agora suba o stack inteiro:"
echo "  ${COMPOSE[*]} up --build -d"
