# BankrotAI Web

Web-клиент BankrotAI: React/Vite, FastAPI, PostgreSQL, Redis, Celery и nginx.
Он использует ту же доменную модель, что и Windows desktop, и покрывает основные
процессы «Поиск», «Реестр», «Карта» и «Сделка». Длительные desktop-операции
(массовые AI/GEO и полный обход всех источников) считаются перенесёнными только
после реализации соответствующих серверных jobs из `docs/ROADMAP.md`.

В онлайн-поиске всех трёх источников регион можно выбрать из списка либо ввести
вручную официальным кодом (`76`) или названием. Категория закреплена как «Вся
недвижимость». Карта использует Яндекс.Карты, desktop-маркеры и статусы проверки
«Интересен / Сомневаюсь / Плохой».

## Frontend для разработки

```powershell
cd WEB
npm ci
npm run dev
```

Vite проксирует `/api` на `http://127.0.0.1:8000`. Для production используйте
только корневой `docker-compose.yml`: он требует пароли PostgreSQL/Redis,
`BANKROTAI_API_KEY` и WEB Basic Auth, не публикует БД/Redis/API на хост и
привязывает web к `127.0.0.1` по умолчанию.

```powershell
Copy-Item .env.example .env
docker compose up -d --build
docker compose ps
```

По умолчанию интерфейс доступен на `http://127.0.0.1:8080` и запрашивает
`WEB_BASIC_AUTH_USER` / `WEB_BASIC_AUTH_PASSWORD`. Для публичного домена нужен
отдельный TLS reverse proxy; менять `WEB_BIND_ADDRESS` на публичный адрес без TLS
и сетевого контроля нельзя.

API проверяет ключ на всех маршрутах, кроме healthchecks, использует общий Redis
rate limit и возвращает `503`, если production-конфигурация небезопасна.

## Проверки

```powershell
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm audit
```

Актуальная архитектура, API и roadmap описаны в корневых `README.md` и
`docs/ROADMAP.md`.
