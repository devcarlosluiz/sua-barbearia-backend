#!/usr/bin/env bash
#
# Emissão inicial do certificado TLS. Roda UMA vez, na VM, antes do primeiro
# `up` completo. Depois disso o serviço `certbot` do compose cuida da renovação.
#
#   LETSENCRYPT_EMAIL=voce@exemplo.com ./scripts/init_letsencrypt.sh
#
# O problema que este script resolve: o nginx não sobe se o arquivo de
# certificado não existir, e o Let's Encrypt não emite o certificado sem um
# nginx no ar respondendo ao desafio HTTP. A saída é subir com um certificado
# auto-assinado descartável e trocá-lo pelo real em seguida.
set -euo pipefail

DOMAIN="${DOMAIN:-teste.clmlabs.com.br}"
# Sem apóstrofo nesta mensagem: dentro de ${VAR:?...} ele abre uma citação e
# quebra o parser do bash.
EMAIL="${LETSENCRYPT_EMAIL:?defina LETSENCRYPT_EMAIL. Este endereço recebe os avisos de expiração do certificado}"
STAGING="${STAGING:-0}"

# Os `-f` explícitos são obrigatórios: sem eles o Compose carregaria o
# docker-compose.override.yml e este script mexeria no stack de desenvolvimento.
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
LIVE="/etc/letsencrypt/live/$DOMAIN"

# O ambiente de staging tem limite de emissão muito maior. Use STAGING=1 para
# ensaiar: o certificado sai inválido para o navegador, mas prova que o
# desafio funciona sem queimar a cota semanal do ambiente real.
staging_flag=()
if [ "$STAGING" != "0" ]; then
    staging_flag=(--staging)
    echo ">> STAGING ligado: o certificado NÃO será confiável no navegador."
fi

echo ">> 1/5 Certificado temporário para o nginx conseguir subir"
"${COMPOSE[@]}" run --rm --entrypoint sh certbot -c "
    mkdir -p '$LIVE' &&
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -keyout '$LIVE/privkey.pem' -out '$LIVE/fullchain.pem' \
        -subj '/CN=$DOMAIN'
"

echo ">> 2/5 Subindo o nginx"
"${COMPOSE[@]}" up -d nginx

echo ">> 3/5 Descartando o temporário"
"${COMPOSE[@]}" run --rm --entrypoint sh certbot -c "
    rm -rf '/etc/letsencrypt/live/$DOMAIN' \
           '/etc/letsencrypt/archive/$DOMAIN' \
           '/etc/letsencrypt/renewal/$DOMAIN.conf'
"

echo ">> 4/5 Pedindo o certificado real ao Let's Encrypt"
"${COMPOSE[@]}" run --rm --entrypoint certbot certbot \
    certonly --webroot -w /var/www/certbot \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --non-interactive \
    "${staging_flag[@]}"

echo ">> 5/5 Recarregando o nginx com o certificado válido"
"${COMPOSE[@]}" exec nginx nginx -s reload

echo
echo "Pronto. Agora suba o stack inteiro:"
echo "  ${COMPOSE[*]} up --build -d"
