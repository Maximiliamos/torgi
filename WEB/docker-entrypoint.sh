#!/bin/sh
set -eu

: "${BANKROTAI_API_KEY:?BANKROTAI_API_KEY is required}"
: "${WEB_BASIC_AUTH_USER:?WEB_BASIC_AUTH_USER is required}"
: "${WEB_BASIC_AUTH_PASSWORD:?WEB_BASIC_AUTH_PASSWORD is required}"

envsubst '${BANKROTAI_API_KEY}' \
  < /etc/nginx/templates/default.conf.template \
  > /etc/nginx/conf.d/default.conf
htpasswd -bc /tmp/bankrotai.htpasswd "$WEB_BASIC_AUTH_USER" "$WEB_BASIC_AUTH_PASSWORD"
chown nginx:nginx /tmp/bankrotai.htpasswd /etc/nginx/conf.d/default.conf

exec su-exec nginx nginx -g 'daemon off;'
