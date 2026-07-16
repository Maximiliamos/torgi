# BankrotAI

BankrotAI — desktop-приложение и web-сервис для сбора, поиска, геокодирования и предварительного AI-анализа лотов банкротных и публичных торгов.

## Архитектура

- `src/bankrotai/gui.py` — PySide6 desktop-интерфейс, импорт файлов и Excel-экспорт.
- `src/bankrotai/api.py` — FastAPI API, healthchecks и постановка массовых задач.
- `src/bankrotai/tasks.py` — Celery worker: синхронизация порциями, retries и прогресс.
- `src/bankrotai/db.py`, `alembic/` — SQLAlchemy и единая цепочка миграций SQLite/PostgreSQL.
- `src/bankrotai/ai.py`, `src/bankrotai/geo.py` — строгая проверка AI-ответов и геокодирование.
- `WEB/` — React/Vite web-клиент.
- `docker-compose.yml` — PostgreSQL, Redis, миграции, API, worker, beat и web.

Закрытые лоты не удаляются: они получают `is_archived=true`, дату архивации и остаются доступны для аудита. Каждая смена статуса записывается в `lot_status_history`.

## Локальная установка

Требуется Python 3.11+.

```bash
python -m venv .venv
python -m pip install -e ".[dev,desktop]"
```

Для запуска только API desktop-зависимости не нужны:

```bash
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
```

Для полного набора инструментов разработки и desktop-тестов используйте
`requirements-dev.lock`.

Основные настройки окружения:

```env
DATABASE_URL=sqlite:///./bankrotai.db
REDIS_URL=redis://localhost:6379/0
APP_ENV=development
ALLOW_LOCAL_TASK_FALLBACK=false
CORS_ORIGINS=http://localhost:8080
```

AI-провайдер задаётся через `AI_PROVIDER` и соответствующие ключ, URL и модель. Автоматический переход на другой провайдер выключен; фактически использованные provider/model сохраняются в БД.

Результат AI — только предварительная машинная гипотеза, а не независимая оценка
имущества и не инвестиционная рекомендация. Любая цена, риск и вывод требуют
проверки оценщиком, юристом и техническим специалистом. API-ключи AI читаются
только из переменных окружения/secret manager и не сохраняются в `app_settings`.

## Desktop и API

```bash
python -m bankrotai.cli run-desktop
python -m bankrotai.cli run-api --host 0.0.0.0 --port 8000
```

SQLite используется по умолчанию. Перед стартом приложение применяет Alembic-миграции; `create_all()` и отключение foreign keys в production-пути не используются.

## Docker

Скопируйте `.env.example` в `.env` и замените все значения `replace-with-*`
независимыми случайными секретами. Compose откажется стартовать без пароля БД,
пароля Redis, API-ключа и WEB Basic Auth.

Полный production-подобный стек:

```bash
docker compose up -d --build
docker compose ps
docker compose down
```

WEB доступен на `http://localhost:8080` с Basic Auth. PostgreSQL, Redis и API
не публикуются на хост; nginx обращается к API по внутренней сети и передаёт
служебный API-ключ. По умолчанию WEB привязан к `127.0.0.1`; публичный bind
следует включать только за TLS reverse proxy. Миграции выполняются одноразовым
сервисом `migrate` до старта API/worker. Контейнеры приложения работают без root,
имеют healthchecks, restart policies и ограничения ресурсов.

Проверки состояния:

- `GET /health/live` — процесс API работает;
- `GET /health/ready` — конфигурация безопасна, БД/схема и Redis доступны;
- `GET /health` — совместимый alias readiness.

## Миграции

```bash
alembic upgrade head
alembic current
```

Для существующей базы сначала сделайте резервную копию, задайте её `DATABASE_URL`, затем выполните `alembic upgrade head`. Миграции сохраняют пользовательские лоты, настройки, watchlist и историю; PostgreSQL и SQLite проверяются отдельно.

## Фоновые задачи

Worker и beat локально:

```bash
celery -A bankrotai.tasks:celery_app worker --loglevel=INFO
celery -A bankrotai.tasks:celery_app beat --loglevel=INFO
```

Синхронный `GET /api/online/torgi-gov/lots` предназначен для ограниченного просмотра: `page_size` и `limit` ограничены API, массовый обход всех страниц через GET запрещён.

Массовая синхронизация:

```http
POST /api/online/torgi-gov/sync
Content-Type: application/json

{"search":"земельный участок","max_items":500}
```

Ответ содержит `task_id`. Прогресс и итог доступны по `GET /api/tasks/{task_id}`. Если Redis/Celery недоступны, production возвращает `503`; локальный thread fallback разрешается только явным `ALLOW_LOCAL_TASK_FALLBACK=true` вне production.

## Тесты и качество

Python/SQLite:

```bash
pytest
ruff check src tests
mypy src/bankrotai
```

PostgreSQL integration и миграции:

```bash
docker compose -f docker-compose.test.yml up -d postgres-test
$env:TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:55432/bankrotai_test"
pytest -m postgres
docker compose -f docker-compose.test.yml down -v
```

WEB:

```bash
npm --prefix WEB ci
npm --prefix WEB run typecheck
npm --prefix WEB run lint
npm --prefix WEB run test
npm --prefix WEB run build
```

Полный Docker smoke (Linux/Git Bash/CI; требует Docker, curl, Python, Node и установленный Chromium Playwright):

```bash
bash scripts/docker-smoke.sh
```

Сценарий поднимает чистый стек, проверяет миграции, liveness/readiness, API, Celery-задачу, WEB и Playwright, затем удаляет контейнеры и тестовые volumes.

## Надёжность AI и GEO

- AI JSON валидируется Pydantic-моделями с диапазонами и обязательными полями; некорректный ответ не записывается как успешный.
- В `valuation_runs` сохраняются provider, model, статус и диагностическая ошибка.
- Геокодер не создаёт фиктивные координаты. Неуспех остаётся явным отсутствием координат.
- TLS-проверка НСПД включена. Ошибки сертификата имеют отдельную диагностическую причину.
- Успешные результаты Nominatim кэшируются, запросы ограничиваются timeout/retry/backoff.

## CI

GitHub Actions запускается для push и pull request в `main`: Ruff/Mypy, SQLite tests, upgrade существующей БД, PostgreSQL integration/Alembic, WEB typecheck/lint/unit/build и Docker/Playwright smoke. Python и npm зависимости устанавливаются по lock-файлам.

## Ограничения

- Внешние TBankrot, torgi.gov.ru, НСПД, Nominatim и AI API могут быть недоступны или менять ответы; ошибки показываются явно и не подменяются вымышленными данными.
- Desktop использует отдельную optional-группу зависимостей и не входит в API Docker image.
- `GET` не предназначен для массовой синхронизации; используйте Celery endpoint и проверку статуса.
