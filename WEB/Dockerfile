FROM node:22-alpine AS build

WORKDIR /app
COPY WEB/package.json ./
RUN npm install
COPY WEB ./
RUN npm run build

FROM nginx:1.27-alpine

COPY WEB/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
