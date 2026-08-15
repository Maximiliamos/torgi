# Бесплатное развёртывание WEB MVP

## Схема

`dezster.ru` → Cloudflare edge Worker → Pages Function `/api/*` → REG.RU FastAPI → Neon PostgreSQL.

`bankrotai.pages.dev` остаётся техническим адресом Pages. Edge Worker из `WEB/edge-proxy/`
обслуживает корневой домен и перенаправляет `www.dezster.ru` на `dezster.ru`. Доступ к REG.RU
должен выполняться через Cloudflare Tunnel; прямой `sslip.io` используется только для диагностики.

Браузер не получает межсервисный ключ. Function добавляет `KOYEB_SERVICE_KEY`, а FastAPI
дополнительно требует персональную HttpOnly-сессию. Production API работает с
`API_READ_ONLY=true` означает curated WEB mode: массовая синхронизация регионов и фоновые
операторские очереди закрыты, но авторизованному пользователю доступны обратимые персональные
операции (watchlist, заметки, сохранённые фильтры, калькулятор и участие). Merge/split требует
роли `admin`. Название переменной сохранено для обратной совместимости и не означает полностью
запрещённые HTTP mutations.

## Neon

Создайте проект и получите две строки подключения:

- `NEON_DATABASE_URL` — pooled hostname с `-pooler`, используется приложением;
- `NEON_DATABASE_MIGRATION_URL` — direct hostname без `-pooler`, используется Alembic,
  `pg_dump` и первоначальным переносом.

Обе строки должны содержать `sslmode=require&channel_binding=require` и использовать схему
`postgresql+psycopg://` для SQLAlchemy.

Первоначальный перенос проверенной SQLite-копии:

```powershell
$env:DATABASE_MIGRATION_URL="postgresql+psycopg://...direct...?sslmode=require&channel_binding=require"
alembic upgrade head
python scripts/migrate_sqlite_to_postgres.py backups/bankrotai-manual-20260801T182354Z.db --copy
python scripts/verify_postgres.py
```

Скрипт требует пустые целевые таблицы, сверяет количество записей во всех таблицах и ищет
осиротевшие внешние ключи. Он намеренно отказывается работать через pooled URL.

## REG.RU origin

Production развёртывается workflow `.github/workflows/regru-deploy.yml` из ветки `main`.
Caddy раздаёт собранный WEB и проксирует `/api/*` в FastAPI на внутреннем Docker network.
Контейнер API публикуется только на `127.0.0.1:8000`; `/health/live` проверяет процесс,
а `/health/ready` — конфигурацию, схему и Neon.

Переменные/секреты:

```text
APP_ENV=production
API_READ_ONLY=true
DATABASE_URL=<NEON_DATABASE_URL>
BANKROTAI_API_KEY=<случайный межсервисный ключ, минимум 24 символа>
AUTH_SESSION_SECRET=<случайный ключ подписи сессий, минимум 32 символа>
API_RATE_LIMIT_PER_MINUTE=60
DATABASE_POOL_SIZE=3
DATABASE_MAX_OVERFLOW=2
DATABASE_POOL_TIMEOUT=10
```

REG.RU Free Tier имеет ограниченные CPU, память и диск. Workflow перед развёртыванием удаляет
неиспользуемые Docker-образы, а контейнеры работают с ограничениями памяти и ротацией журналов.
Критические ошибки дополнительно пишутся в `diagnostic_events`.

## Cloudflare Pages

Подключите тот же GitHub-репозиторий:

- Root directory: `WEB`
- Build command: `npm ci && npm run build`
- Build output: `dist`
- Plain variable `KOYEB_API_ORIGIN=https://<резервный-origin>` — имя сохранено для обратной совместимости
- Encrypted secret `KOYEB_SERVICE_KEY`, совпадающий с `BANKROTAI_API_KEY` на REG.RU

Pages Function находится в `WEB/functions/api/[[path]].ts`. В настройках Runtime выберите
fail closed, поскольку Function является частью границы авторизации. Основной публичный адрес —
`https://dezster.ru`; `*.pages.dev` используется для диагностики и аварийного доступа.

## GitHub Actions

Workflow `.github/workflows/neon-sync.yml` запускается каждые шесть часов и вручную. Добавьте:

Secrets:

- `NEON_DATABASE_URL`
- `NEON_DATABASE_MIGRATION_URL`
- `KOYEB_SERVICE_KEY`
- `AUTH_SESSION_SECRET`
- `AUTH_BOOTSTRAP_PASSWORD`
- `BACKUP_ENCRYPTION_PASSWORD`
- `PUBLIC_WEB_URL` — для smoke workflow
- `CLOUDFLARE_API_TOKEN` — отдельный scoped token только для ручного workflow
  `Deploy Cloudflare edge proxy`; tunnel token для этого не подходит

Variable: `AUTH_BOOTSTRAP_USERNAME`.

Workflow имеет единый concurrency lock, timeout 50 минут и создаёт GitHub Issue при ошибке.
Каждый успешный запуск сохраняет зашифрованный dump на три дня.

`Public WEB smoke` каждые шесть часов быстро проверяет канонический `https://dezster.ru`.
`Production functional reliability` ежедневно выполняет отдельный реальный read-only journey
через Cloudflare: auth, реестр, три внешних источника, GEO, изображения, ссылки ЭТП и карту.
REG.RU deploy устанавливает минутный liveness-watchdog; после трёх последовательных зависаний
он перезапускает API и tunnel, а единичная ошибка readiness только журналируется.

## Восстановление

1. Остановите scheduled workflow и временно не запускайте сборщик.
2. Для ошибки младше шести часов используйте Neon Restore/Time Travel в отдельную ветку и
   проверьте данные до переключения.
3. Для encrypted artifact скачайте файл и расшифруйте:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 \
  -in bankrotai-RUN_ID.dump.enc -out bankrotai.dump \
  -pass env:BACKUP_ENCRYPTION_PASSWORD
PG_RESTORE_URL="${DATABASE_MIGRATION_URL/postgresql+psycopg/postgresql}"
pg_restore --clean --if-exists --no-owner --no-acl \
  -d "$PG_RESTORE_URL" bankrotai.dump
alembic upgrade head
python scripts/verify_postgres.py
```

4. Проверьте `/health/ready`, выполните public Playwright smoke и только затем возобновите cron.
