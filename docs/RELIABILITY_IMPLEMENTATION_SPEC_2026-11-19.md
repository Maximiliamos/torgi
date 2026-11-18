# BankrotAI — ТЗ на доработку раздела «Надёжность»

**Версия:** 1.0  
**Дата:** 19.11.2026  
**Основа аудита:** `main@bbc3d75018d281aca3ef938c1c360fc5c8519413`

## 1. Цель

Сделать раздел «Надёжность» операционно достоверным: каждое состояние имеет однозначный смысл, единый scope, актуальное время и подтверждаемую цепочку данных.

Нельзя менять данные лотов, Source/Canonical связи, GEO facts или выполнять mass UPDATE/DELETE в рамках этой задачи.

## 2. Подтверждённые дефекты

| ID | Дефект | Приоритет |
|---|---|---|
| R-01 | Общий зелёный статус зависит только от global network circuit | P0 |
| R-02 | `0/0` источников из-за исключения paused из denominator | P0 |
| R-03 | healthy + историческая ошибка после более позднего success | P0 |
| R-04 | aliases создают дубли источников | P0 |
| R-05 | MapDataset ready не доказывает public delivery | P1 |
| R-06 | current GEO смешан с historical LotGeoSnapshot | P1 |
| R-07 | полнота считается по всей исторической ProcessedLot | P1 |
| R-08 | runner snapshot ошибочно трактуется как online/offline | P1 |
| R-09 | disk healthy начинается с 15 ГБ при рекомендации ≥25 ГБ | P1 |
| R-10 | operational/map-delivery GET отсутствуют в read-only whitelist | P0 |
| R-11 | frontend показывает английские enum | P1 |
| R-12 | diagnostics рендерится raw JSON вместо скачивания | P2 |

## 3. Новый API

### GET `/api/reliability/summary`

Read-only, `require_user`, доступен в production read-only.

Возвращает единый snapshot:

- `schema_version`;
- `generated_at`;
- `system`;
- `sources`;
- `geo`;
- `map`;
- `integrity`;
- `host`;
- `data_quality`;
- `journal`.

### GET `/api/reliability/sources`

Возвращает canonical source rows:

- canonical_id;
- display_name_ru;
- aliases[];
- configured / scheduled / role;
- paused;
- overall_status;
- freshness / coverage / circuit;
- current_error;
- last_historical_error;
- retry_overdue;
- last run counters.

### GET `/api/reliability/map-delivery`

Read-only map reconciliation.

Публичный manifest проверяется с серверным кешем, чтобы UI refresh раз в 30 секунд не создавал внешний S3-запрос каждый раз.

## 4. Source semantics

### Canonical projection

Current configured source IDs берутся из `default_source_specs()`.

Исторические aliases агрегируются в canonical rows. На первом этапе запрещена destructive migration старых source_system.

Известные aliases включают:

- `TBankrot` → `tbankrot.ru`;
- `ГИС Торги` → `torgi.gov.ru`;
- `РАД / ЛОТ-ОНЛАЙН` → `lot-online.ru`.

### Current error

`current_error` присутствует только если failure новее последнего success либо последний run остаётся failed.

После более позднего success ошибка остаётся только в `last_historical_error`.

### Overall source status

1. paused → `paused`;
2. current error / failed / circuit open|half_open → `failed`;
3. partial / stale / delayed / coverage missing|stale / retry overdue → `degraded`;
4. fresh + acceptable coverage + no current error → `healthy`;
5. нет запусков/данных → `not_checked`.

## 5. Общий статус системы

### incident

Минимум одно:

- map delivery integrity broken;
- критичный DB invariant;
- backup не имеет свежего подтверждённого restore;
- диск <15 ГБ;
- другой явно критический обязательный компонент.

### degraded

- configured source failed/degraded/not_checked;
- не все full snapshots актуальны;
- GEO paused или имеет actionable tail;
- runner diagnostic stale;
- disk 15–25 ГБ;
- карта ещё не полностью подтверждена.

Один failed pilot/historical source сам по себе не должен переводить всю систему в incident.

## 6. GEO

Reliability KPI использует scope `geocoding_progress()`:

- primary;
- non-archived;
- есть адрес или кадастр.

Исторический `LotGeoSnapshot` возвращается только как отдельная справочная метрика.

## 7. Data quality current scope

Считать только:

- `duplicate_of_id IS NULL`;
- `is_archived = false`;
- status не входит в ended statuses.

Поля:

- current_population;
- missing_address;
- missing_cadastre;
- missing_price;
- unknown_status;
- missing_region;
- missing_url;
- non_positive_price;
- historical_total отдельно.

Каждая missing-метрика: `count + percent`.

## 8. Map delivery

Использовать `map_delivery_reconciliation_report()`.

Зелёный статус карты допускается только если:

- ровно один current dataset;
- dataset ready и published;
- point count совпадает;
- tile count совпадает;
- нет непредусмотренных missing/extra;
- public manifest проверен и совпадает.

Deep public verification кешировать минимум на 3–5 минут.

## 9. Integrity

Минимальные проверки:

- Source без Processed;
- Processed без Source;
- Source одновременно active + archived;
- archived Processed без archived_at;
- first_seen > last_seen;
- application_start > deadline;
- archived_at < first_seen;
- current dataset count;
- tile mismatch;
- point mismatch;
- map missing/extra.

UI показывает число **классов** critical/warning и раскрывает конкретные counts.

## 10. Host / backup / runner

### Диск

- ≥25 ГБ → ok;
- 15–25 → warning;
- <15 → critical.

### Backup

Healthy только если:

- snapshot существует;
- `restore_verification == passed`;
- возраст ≤60 часов.

### Runner

Переименовать в runner diagnostic.

Stale diagnostic snapshot не означает offline runner.

## 11. Read-only production

В whitelist добавить:

- `/api/reliability/summary`;
- `/api/reliability/sources`;
- `/api/reliability/map-delivery`;
- при необходимости существующие `/api/quality/operational` и `/api/quality/map-delivery`.

Новые Reliability GET не должны открывать write-возможности.

## 12. Frontend

Старый монолитный debug-screen заменить на блоки из дизайн-ТЗ.

Обязательно:

- русские labels;
- общий hero status;
- 8 KPI;
- attention list;
- searchable/filterable canonical sources;
- GEO current funnel;
- map delivery chain;
- current-scope data quality;
- infrastructure;
- integrity;
- journal filters;
- diagnostics download/copy.

Raw `Object.entries(quality)` удалить.

## 13. Performance

- `/api/reliability/summary` не должен делать некешированный внешний HTTP-вызов каждые 30 секунд.
- Manifest verification — server-side cache.
- Source probe никогда не запускается автоматически для всех источников.
- UI refresh — 30 секунд.

## 14. Тесты

### Backend

- current_error закрывается более поздним success;
- aliases схлопываются;
- paused configured source остаётся в denominator;
- current data quality не включает archived/ended;
- disk tri-state;
- read-only Reliability GET = 200;
- map ready при manifest mismatch не даёт ok.

### WEB

- нет raw enum в primary UI;
- source failures сортируются/фильтруются;
- diagnostics скачивается;
- stale/historical errors визуально разделены;
- mobile layout не переполняется.

### CI

Должны пройти обязательные protected checks:

- Python lint and types;
- Python tests SQLite;
- migration tests;
- PostgreSQL integration;
- WEB typecheck/lint/tests/build;
- Docker read-only production smoke;
- Docker operator queue smoke;
- Windows BAT smoke.

## 15. Definition of Done

- Глобальный статус не зависит только от network circuit.
- Нет `0/0` при существующих configured sources.
- Healthy source не показывает закрытую историческую ошибку как текущую.
- Aliases не создают отдельные source rows.
- Primary UI полностью русский.
- GEO current/history разведены.
- Current data quality имеет единый знаменатель и проценты.
- Карта зелёная только после delivery reconciliation.
- Reliability GET работают в read-only production.
- Diagnostics скачивается/копируется.
- Все protected CI checks зелёные.
- Production manual acceptance выполнена на desktop/tablet/mobile.
