# BankrotAI Web

Онлайн-версия BankrotAI: React frontend, FastAPI backend, PostgreSQL, Redis, Celery worker и nginx.

## Локальный запуск frontend

```bash
cd WEB
npm install
npm run dev
```

Vite проксирует `/api` на `http://127.0.0.1:8000`.

## Полный запуск через Docker

```bash
cd WEB
cp .env.example .env
docker compose up --build -d
```

После запуска приложение будет доступно на порту из `WEB_PORT`, по умолчанию `80`.

## Настройки для домена

1. В `WEB/.env` заменить:
   - `POSTGRES_PASSWORD`;
   - `CORS_ORIGINS=https://your-domain.ru`;
   - `BANKROTAI_API_KEY`;
   - AI-провайдера и ключи.
2. В DNS домена указать `A`-запись на IP VPS.
3. Перед контейнером можно поставить Caddy/Nginx/Traefik для HTTPS.

Пример Caddyfile на VPS:

```caddy
your-domain.ru {
    reverse_proxy 127.0.0.1:80
}
```

Если порт `80` занят reverse proxy, задайте в `WEB/.env`:

```env
WEB_PORT=8080
```

и проксируйте домен на `127.0.0.1:8080`.
