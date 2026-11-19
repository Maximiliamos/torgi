# Техническое задание: доработка раздела «Надёжность»

Версия 1.0 · 19.11.2026 · BankrotAI Web  
Связанный документ: [ТЗ на дизайн](./RELIABILITY_DESIGN_TZ_2026-11-19.md)

## 1. Подтверждённые проблемы
| ID | Проблема | Приоритет |
|---|---|---|
| R-01 | Общий статус по одному `network.circuit_open` | P0 |
| R-02 | `0/0` из-за denominator только active configured | P0 |
| R-03 | Healthy + старый `last_error` после success | P0 |
| R-04 | Исторические алиасы/дубли source rows | P0 |
| R-05 | MapDataset ready ≠ public delivery | P1 |
| R-06 | GEO current ≠ исторический LotGeoSnapshot | P1 |
| R-07 | `/api/quality` исторический scope для «Полноты данных» | P1 |
| R-08 | Runner diagnostic snapshot ≠ runner online | P1 |
| R-09 | Порог диска 25 ГБ в UI, backend healthy от 15 ГБ | P1 |
| R-10 | `/api/quality/operational`, `/api/quality/map-delivery` не были в read-only whitelist | P0 |
| R-11 | Сырые английские enum в UI | P1 |
| R-12 | «Экспорт» показывал raw JSON вместо скачивания | P2 |

## 2. Целевой API
Рекомендуется агрегированный **GET /api/reliability/summary** (require_user, read-only, schema_version=1) с единым generated_at, scope и блоками:
- system: status, reasons, deployment_sha, app_version, schema_revision;
- sources: configured_total, scheduled_total, paused_total, healthy/degraded/failed/stale, last complete snapshot;
- geo: population, current_geocoded, percent, actionable, deferred_*;
- map: dataset_version, point_count, db/dataset/S3/public checks, mismatch counts, published_at;
- integrity: critical/warning counts, Source↔Processed, даты, цены, дубли, tile mismatch;
- host: API/DB/queue/disk/backup/runner_diagnostic;
- data_quality: current primary non-archived non-ended population + missing counts/percentages.

**GET /api/reliability/sources**: canonical_id, display_name_ru, aliases[], configured/scheduled/pilot/historical_only, status, freshness, coverage, last_success, current_error, last_historical_error, retry_overdue. Не проводить destructive migration для aliases.

## 3. Семантика
- current_error = ошибка последней неудачи только если более поздний успех её не закрыл.
- Итоговый source status: paused; failed (последняя неудача или активный circuit); degraded (stale/partial/coverage/retry overdue); healthy (fresh + coverage + нет актуальной ошибки); not_checked.
- Global system status: incident при API/DB/критичных invariants/map mismatch/критичном backup; degraded при проблемах источников/GEO/runner/disk; ok только если все обязательные домены подтверждены. Пилотный источник не обязан создавать глобальный incident.
- Источник истины для configured: `default_source_specs()`. На момент аудита — 5 core + 3 scheduled ETP = 8, **не путать** с connector registry и историческими sources.

## 4. GEO, карта, качество
- GEO верхний KPI — `geocoding_progress()` по текущей eligible primary non-archived выборке.
- `LotGeoSnapshot` — исторический показатель, отдельная подпись.
- Карта: `map_delivery_reconciliation_report()`; public manifest проверки кешировать 3–5 минут.
- Качество: новый current-scope snapshot, не менять контракт исторического `/api/quality`.
- Подсчитывать проценты от одного знаменателя; пересекающиеся категории GEO явно обозначить.

## 5. Инфраструктура
- Диск: ≥25 ГБ ok, 15–25 warning, <15 critical.
- Backup: age, restore verification, size, threshold; «проверено» только при успешном restore test.
- Runner: `runner_diagnostic` и checked_at; не выдавать stale snapshot за offline.
- Read-only: разрешить только необходимые GET; write остаются запрещёнными.

## 6. Frontend
- Компоненты: ReliabilityHero, KPI, AttentionList, SourcesTable, GeoPanel, MapDeliveryPanel, DataQualityPanel, InfrastructurePanel, Journal.
- Русский словарь enum и названий полей, unknown fallback.
- Один refresh в page header; автообновление 30 сек.
- `Promise.allSettled`/частичный отказ с сохранением последнего успешного блока.
- Фильтры источников, раскрываемая история ошибок, скачивание JSON диагностики.
- Не показывать ложный зелёный статус до завершения map delivery и integrity проверок.

## 7. Тесты
1. Backend unit: current_error cleared after success; aliases; configured count; severity; disk thresholds.
2. API: schema version, read-only GET 200, write 404/403, require_user.
3. PostgreSQL integration: current-scope quality, Source↔Processed, map delivery.
4. Frontend: русские labels, stale/failed sorting, historic error hidden, no `0/0`, diagnostics download.
5. CI: Python lint/types, SQLite, PostgreSQL, WEB typecheck/lint/tests/build, Docker read-only smoke.
6. Production: скриншоты desktop/tablet/mobile, подтверждение фактических значений, deployment SHA.

## 8. Приоритеты и ограничения
- **P0:** исправить смысл статусов, aliases, source counts, API whitelist.
- **P1:** GEO scope, quality current scope, map delivery, infrastructure, responsive UX.
- **P2:** журнал, фильтры, экспорт, дополнительные детали.
- Не изменять Lot/Source/Canonical данные и не запускать mass UPDATE/DELETE.
- Сохранять исторические данные и обратную совместимость API.
- Не считать этап завершённым без зелёного CI и проверки в production.

## 9. Статус реализации
Этот документ — целевое ТЗ, **не декларация о завершении**. Частичная WEB-реализация и read-only whitelist поставляются отдельными коммитами в PR. Полноценные агрегированные endpoints, canonical projection и DB/S3 delivery reconciliation требуют последующей реализации и проверки.
