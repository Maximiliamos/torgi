FROM node:22-alpine AS build

WORKDIR /app
COPY WEB/package.json WEB/package-lock.json ./
RUN npm ci
COPY WEB ./
RUN npm run build

FROM nginx:1.27-alpine

RUN apk add --no-cache apache2-utils gettext su-exec \
    && sed -i 's#pid .*;#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf \
    && chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx

COPY WEB/nginx.conf /etc/nginx/templates/default.conf.template
COPY WEB/docker-entrypoint.sh /usr/local/bin/bankrotai-web-entrypoint
COPY --from=build /app/dist /usr/share/nginx/html

RUN chmod 755 /usr/local/bin/bankrotai-web-entrypoint

EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/bankrotai-web-entrypoint"]
