# BankrotAI Pro

BankrotAI Pro - настольное приложение на PySide6 и локальный FastAPI API для поиска, импорта, геокодирования и AI-оценки лотов на торгах по банкротству и публичных торгах.

Документация обновлена после аудита проекта 2026-06-03 и отражает текущее состояние исходников, конфигурации и тестов.

## Реальное состояние

Работает и подтверждено кодом:

- реестр лотов в GUI с поиском, фильтрами, сортировкой по столбцам и экспортом в Excel;
- импорт сохраненных HTML-страниц TBankrot;
- онлайн-поиск `torgi.gov.ru` через JSON API, HTML fallback и Excel-выгрузку;
- онлайн-поиск `tbankrot.ru` во вкладке `Поиск Т Банкрот` с фильтрами из формы сайта;
- синхронизация публичных источников TBankrot/GorodTorgi;
- локальный FastAPI API;
- SQLite по умолчанию, PostgreSQL через `DATABASE_URL`;
- Alembic-миграции для схемы БД;
- AI-оценка рыночной цены, риска, дисконта и рейтинга;
- GUI-выбор AI-провайдера: `omniroute` (Kiro через OmniRoute) и `nvidia`;
- кадастровый поиск по номеру и адресу;
- карта Leaflet/OpenStreetMap с кластеризацией маркеров;
- локальный WMS-прокси для НСПД/Росреестр-подложки;
- сохранение геоснимков в `lot_geo_snapshots`, включая `geometry_json` и `metadata_json`;
- массовая AI/GEO-обработка в фоновых потоках с отдельными прогресс-барами `AI` и `GEO`;
- тестовый набор: 49 тестов проходят на текущей машине.

Важные ограничения текущего состояния:

- `.env.example` устарел: содержит Telegram/TBankrot-переменные старого контура и не отражает OmniRoute/NVIDIA/CORS-настройки из текущего кода.
- `docker-compose.yml` содержит реальные сервисы `api`, `worker`, `beat`, `postgres`, `redis`, `migrate`, но `src/bankrotai/tasks.py` пока жестко использует `redis://localhost:6379/0`, а не `REDIS_URL`; в Docker это может ломать Celery worker/beat.
- `pyproject.toml` не объявляет все фактически используемые зависимости: в коде есть `tenacity`, `lxml` и опционально `anthropic`.
- `pip check` в текущем окружении не чистый: установлен `openai 2.32.0`, а проект требует `openai<2`; также есть конфликт `aiogram` и `pydantic 2.13.3`.
- В репозитории присутствуют локальные артефакты и данные: БД, Excel/CSV/JSON-выгрузки, `build/`, `dist/`, `omniroute_data/`, логи и кэши. Они отражают рабочее состояние папки, но не являются чистым исходным кодом.
- `start_bankrotai.bat` завязан на абсолютные пути `D:\8\Coding\TORGI_NEW`, `D:\8\Coding\omniroute` и опционально `D:\8\Coding\hindsight`.

## Структура проекта

- `src/bankrotai/gui.py` - PySide6 GUI, таблица лотов, вкладки, карта, массовые worker-задачи, AI/GEO обработка, Excel-экспорт.
- `src/bankrotai/api.py` - FastAPI API, CORS, эндпоинты лотов, статистики, онлайн-поиска и синхронизации регионов.
- `src/bankrotai/db.py` - SQLAlchemy-модели, engine, sessions, SQLite WAL/busy timeout, `DB_WRITE_LOCK`, Alembic upgrade при `init_db()`.
- `src/bankrotai/domain.py` - dataclass-модели нормализованного лота и AI-оценок.
- `src/bankrotai/logic.py` - классификация, расчет дисконта/рейтинга, сохранение лотов, ответы API, удаление лотов.
- `src/bankrotai/scrapers.py` - TorgiGovClient, TBankrotClient, GorodTorgiClient, HTML-парсер и нормализация публичных источников.
- `src/bankrotai/geo.py` - PKK/НСПД/Nominatim-поиск, GeoJSON, сохранение геоснимков.
- `src/bankrotai/ai.py` - OpenAI-compatible AI-провайдеры, OmniRoute, NVIDIA, JSON-разбор, retries и кэш `valuation_runs`.
- `src/bankrotai/tasks.py` - Celery-задача синхронизации и локальный thread fallback, если Redis недоступен.
- `src/bankrotai/extractors.py` - извлечение цены, площади, адреса, кадастровых номеров и технических параметров из текста.
- `alembic/` - миграции базы.
- `tests/` - pytest-набор на scoring, geo, torgi.gov, TBankrot, публичные источники, GUI-сортировку и OmniRoute.
- `mass_valuation.py`, `run_system_check.py`, `fix_provider.py`, `list_omni_models.py` - вспомогательные скрипты.
- `BankrotAI.spec`, `build/`, `dist/` - PyInstaller-сборка и артефакты.

## Запуск

Из корня проекта:

```bat
set PYTHONPATH=src
python -m bankrotai.cli run-desktop
```

Запуск API:

```bat
set PYTHONPATH=src
python -m uvicorn bankrotai.api:app --port 8000
```

Альтернативно через CLI:

```bat
set PYTHONPATH=src
python -m bankrotai.cli run-api --host 0.0.0.0 --port 8000
```

Полный локальный запуск с OmniRoute и API:

```bat
src\bankrotai\start_bankrotai.bat
```

Этот bat-файл запускает OmniRoute, backend API, опционально Hindsight через Docker, затем GUI. Он рассчитан на текущую локальную структуру папок и не является универсальным portable-лаунчером.

## Настройки

Код читает `.env` через `python-dotenv`.

Основные переменные для текущего контура:

```env
DATABASE_URL=sqlite:///./bankrotai.db
DEFAULT_REGION_SLUG=yaroslavl
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

AI_PROVIDER=omniroute
OMNIROUTE_API_KEY=sk_omniroute
OMNIROUTE_API_BASE=http://localhost:20128
OMNIROUTE_MODEL=kr/claude-sonnet-4
OMNIROUTE_PROTOCOL=openai

NVIDIA_API_KEY=
NVIDIA_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1
```

Для Docker/PostgreSQL ожидаемый формат:

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/bankrotai
```

Сейчас `REDIS_URL` в `.env.example` не используется кодом Celery: broker/backend зашиты в `tasks.py` как `redis://localhost:6379/0`.

## GUI

GUI запускается из `bankrotai.gui` и содержит:

- таблицу лотов;
- инструменты онлайн-синхронизации;
- импорт HTML-файлов;
- вкладку `Поиск ГИС Торги` для поиска torgi.gov.ru;
- вкладку `Поиск Т Банкрот` для поиска tbankrot.ru;
- Excel-экспорт;
- массовую AI-оценку;
- массовое геокодирование;
- обслуживание базы;
- настройки AI-провайдера;
- вкладку `Карта и Кадастр`.

AI-провайдеры в выпадающем списке GUI:

- `Kiro через OmniRoute`;
- `NVIDIA`.

В `ai.py` также остались реализации OpenAI, DeepSeek, Grok, OpenCode и Kiro, но GUI сейчас оставляет пользователю только OmniRoute/Kiro и NVIDIA.

## Карта и кадастр

Вкладка `Карта и Кадастр` умеет:

- показывать лоты с сохраненными координатами;
- обновлять маркеры лотов;
- включать кадастровую подложку через локальный WMS-прокси;
- искать объект по кадастровому номеру;
- искать адрес через Nominatim;
- подставлять локальные подсказки для адресов Ярославля/Рыбинска;
- рисовать границы объекта, если кадастровый API вернул GeoJSON.

Приоритет геокодирования:

1. кадастровый номер через PKK/НСПД;
2. адрес через Nominatim.

Кадастровые данные сохраняются в `lot_geo_snapshots`:

- `centroid_lat`, `centroid_lon`;
- `geometry_json`;
- `metadata_json`;
- `geo_source`;
- `geo_method`;
- `geo_confidence`;
- `trace_reason`.

## AI-оценка

`OpenAIAppraiser` делает две оценки:

- рыночная цена (`market_price`, `min_price`, `max_price`, `confidence`, `explanation`, `links`);
- риск (`risk_score`, `recommendation`, `time_to_sell`).

Особенности:

- ответ AI должен быть валидным JSON;
- невалидный JSON превращается в явную ошибку с фрагментом ответа;
- retries выполняются для сетевых ошибок, таймаутов, 429 и 5xx;
- кэш оценки хранится в `valuation_runs` и учитывает ID, цену, provider/model, title, description, address, cadastral number, area и category;
- есть sanity-floor для некоторых типов недвижимости, чтобы слишком низкая AI-оценка не проходила без корректировки;
- результат записывается в поля `market_price`, `discount_percent`, `risk_score`, `ai_recommendation`, `rating`, `links_to_analogs`, `needs_human_review`.

## API

Базовые эндпоинты:

- `GET /`
- `GET /health`
- `GET /api/lots`
- `GET /api/online/torgi-gov/lots`
- `GET /api/stats`
- `POST /api/regions/{city_slug}/sync`
- `GET /api/regions/{city_slug}/sync-status`

`/api/lots` поддерживает:

- `city_slug`;
- `page`, `per_page`;
- `search`;
- `categories`, `statuses`;
- `min_price`, `max_price`;
- `min_discount`, `max_discount`;
- `min_risk`, `max_risk`;
- `sort=recommended|price_asc|price_desc|discount|newest`.

`/api/online/torgi-gov/lots` поддерживает:

- `search`;
- `region`;
- `category`;
- `price_min`, `price_max`;
- `notice_status`;
- `lot_status`;
- `page`, `page_size`;
- `all_pages`;
- `limit`;
- `diagnostics`.

## Docker

`docker-compose.yml` содержит:

- `postgres`;
- `redis`;
- `migrate`;
- `api`;
- `worker`;
- `beat`.

Сервисы старого Telegram-бота и отдельного frontend в compose отсутствуют.

Текущее замечание: Celery broker/backend в коде не берутся из `REDIS_URL`; для Docker это нужно исправить перед надежным использованием worker/beat.

## Проверки

Быстрая компиляция:

```bat
python -m compileall -q src tests mass_valuation.py run_system_check.py fix_provider.py list_omni_models.py test_kiro_connection.py test_omni_curl.py
```

Полный pytest:

```bat
set PYTHONPATH=src
python -m pytest -q
```

Результат аудита 2026-06-03:

```text
49 passed, 1 warning in 4.32s
```

Предупреждение pytest связано с записью `.pytest_cache`:

```text
PytestCacheWarning: could not create cache path ... .pytest_cache\v\cache\nodeids
```

`pip check` на текущей машине:

```text
aiogram 3.27.0 has requirement pydantic<2.13,>=2.4.1, but you have pydantic 2.13.3.
bankrotai-finder 0.1.0 has requirement openai<2,>=1.50.0, but you have openai 2.32.0.
```

## Замечания аудита

Приоритетные замечания:

1. Исправить Docker/Celery: читать broker/backend из `REDIS_URL`, иначе worker/beat в compose могут обращаться к `localhost`, а не к сервису `redis`.
2. Обновить `.env.example` под текущий набор переменных и удалить устаревшие Telegram-настройки, если Telegram-контур больше не используется.
3. Синхронизировать зависимости в `pyproject.toml`: добавить `tenacity`, `lxml`, опционально `anthropic`; проверить ограничение `openai<2` или привести установленную версию к нему.
4. Очистить рабочее дерево от локальных артефактов или вынести их в отдельное хранилище: БД, логи, Excel/CSV/JSON-выгрузки, `build/`, `dist/`, `omniroute_data/`.
5. Проверить `.pytest_cache`: сейчас тесты проходят, но pytest не может нормально записать cache path.
6. В GUI есть подпись `Искусственный интеллект (OpenAI)`, хотя фактический пользовательский выбор сейчас OmniRoute/Kiro и NVIDIA; подпись лучше переименовать.
7. В `CADASTRAL_INTEGRATION.md` часть описания устарела: там указан только PKK endpoint и пример обращения к `CadastralObjectResult` как к dict, хотя текущий код использует dataclass и также НСПД/Nominatim.

Текущее резюме: проект исполняемый, тесты проходят, GUI/API/парсеры/AI/геомодуль присутствуют. Главные риски не в базовой компиляции, а в чистоте окружения, Docker/Celery-настройке, актуальности `.env.example` и накопленных локальных артефактах.
