# Бесплатное развёртывание WEB MVP

## Схема

`*.pages.dev` → Cloudflare Pages Function `/api/*` → Koyeb FastAPI → Neon PostgreSQL.

Браузер не получает межсервисный ключ. Function добавляет `KOYEB_SERVICE_KEY`, а FastAPI
дополнительно требует персональную HttpOnly-сессию. Production API работает с
`API_READ_ONLY=true`: доступны только авторизация, список/карточка/процедура лота и статистика.

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

## Koyeb

Создайте один Free Web Service из GitHub-репозитория, Dockerfile из корня, порт `8000`, route `/`.
Run command уже задан Dockerfile. Настройте HTTP health check `/health/live`; `/health/ready`
проверяет конфигурацию, схему и Neon, но не требует Redis в read-only режиме.

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

Free Instance имеет ограниченные ресурсы и засыпает при простое; первый запрос после сна может
занять несколько секунд. В Koyeb runtime logs сохраняется журнал API-ошибок, а критические ошибки
дополнительно пишутся в `diagnostic_events`.

## Cloudflare Pages

Подключите тот же GitHub-репозиторий:

- Root directory: `WEB`
- Build command: `npm ci && npm run build`
- Build output: `dist`
- Plain variable `KOYEB_API_ORIGIN=https://<app>.koyeb.app`
- Encrypted secret `KOYEB_SERVICE_KEY`, совпадающий с `BANKROTAI_API_KEY` в Koyeb

Pages Function находится в `WEB/functions/api/[[path]].ts`. В настройках Runtime выберите
fail closed, поскольку Function является частью границы авторизации. На бесплатном этапе
используйте выданный домен `*.pages.dev`; собственный домен не требуется.

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

Variable: `AUTH_BOOTSTRAP_USERNAME`.

Workflow имеет единый concurrency lock, timeout 50 минут и создаёт GitHub Issue при ошибке.
Каждый успешный запуск сохраняет зашифрованный dump на три дня.

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
