# STERDEZ — аудит расписания 09:00/21:00, 22 ЭТП и публикации карты

Проверено: 04.11.2026. Основа изменений: main b474f4ecf9b40fa2bc77a6629bb08fca9e9cfdb7. **Этот документ описывает код и план, а не успешный фактический production-run.** Доступа к Home PostgreSQL и Windows Celery Beat из GitHub нет; публичный сайт sterdez.online не удалось открыть из доступного веб-окружения, поэтому HTTP/DOM карта live не удостоверена.

## Текущая и предлагаемая схема

| Триггер (Europe/Moscow) | Реализация | Гарантия / ограничение |
|---|---|---|
| 08:55, 20:55 | Celery beat: `probe-source-network-health`, 05:55/17:55 UTC | Обход circuit / health, не full ingestion |
| **09:00, 21:00** | Celery beat: `refresh-nationwide-sources-full`, 06:00/18:00 UTC | Штатный полный sync только default_source_specs: 5 прежних source IDs, исключая runtime paused/circuit; новые профили в старом коде не включены |
| **09:00, 21:00** | **Новая** Celery beat задача `audit-all-direct-etp-profiles` | **21 новый ETP** проходит только bounded read-only QA: до 2 страниц и до 3 кандидатов на страницу, **без GEO и записи лотов**. Это не полный обход, не source acceptance и не успешный production-импорт |
| каждые 5 минут | `geocode-pending-lots` | Точная геопривязка идёт асинхронно; задание сканирования может завершиться до окончания GEO |
| каждую минуту | `publish-dirty-map-dataset` | После изменений в DB/GEO Redis dirty-key, 180 сек тишины, 600 сек минимальный интервал, 900 сек предельная задержка, затем сборка immutable MapDataset и REG.RU S3 |
| после полного ручного Workflow | `phase3-lite-full-reconcile.yml` | Проверяет фактическую полноту full-source runs, публикацию MapDataset и публичный S3 manifest. Workflow не запускается по cron 09/21, только dispatch или соответствующий push |

### Почему пока не разрешено записывать 21 новую ЭТП

В `default_source_specs` теперь добавляется `accepted_etp_source_specs()`, но **ACCEPTED_ETP_FOR_AUTOMATIC_INGEST пока пуст**. Вступление каждого нового ETP требует отдельного review с доказательствами 2 full read-only циклов, источников individual detail, цены, статуса, terminal page, rollback, exact-SHA и качество GEO. `rts-tender.ru` и `utender.ru` blocked; `electro-torgi.ru` runnable в отдельной кампании, но HTTP detail контракт не доказан. Кроме того, даже среди runnable generic провайдеров большинство не имеют capability `detail_enrichment`, поэтому не могут быть просто добавлены в production schedule.

**Новая защита:** неизвестный/заблокированный ETP или коннектор без обязательной проверки individual lot detail не допускается к автоматическому production-сканированию. Одностраничный `fast` режим для будущих accepted ETP отключён, чтобы не перезаписывать подтверждённые поля неполной выдачей. Вся 21-площадочная QA видна в отдельной задаче с `qa_only=true, database_writes=0, production_map_writes=0, complete_source_inventory=false`; базовый Lot-online остаётся в штатном полном sync.

**Важное ограничение:** задачи Celery beat выставляются в очередь в 09:00/21:00 по Москве, но фактическое начало может опоздать из-за загрузки очереди, старых работ, сетевых ошибок или остановки Home. Успех поставленного задания нельзя принимать за доказательство завершения обхода. Время сервера должно быть синхронизировано и Celery реально запущен в UTC.

## Как работает действующая цепочка публикации карты

1. Celery Beat вызывает `automatic_nationwide_lot_refresh_task("full")`, создаёт durable run и блокирует второй конфликтующий sync по PostgreSQL lease.
2. `nationwide_lot_sync_task` использует `default_source_specs()` после pause/circuit filter. Каждая страница upsert в `SourceLot / ProcessedLot`; при полной корректной выдаче могут архивироваться отсутствующие лоты **только для ранее поддерживаемых reconcile-источников**.
3. При `success` или `partial` и изменениях, влияющих на карту, `_mark_map_dirty` выставляет dirty-generation в Redis. Ошибку записи dirty-кода сценарий лишь логирует; автоматическое подтверждение публикации не встроено в каждые 09/21.
4. Геокодер раз в 300 секунд извлекает pending. Когда `geocoded>0`, снова помечает карту dirty.
5. Минутный watchdog потребляет dirty-generation и отправляет сборку новой версии `MapDataset`. Отложенный механизм объединяет мелкие изменения, может собрать карту **до завершения** долгой очереди GEO, а последующие геокоды вызывают следующую публикацию.
6. `build_map_dataset_task` сравнивает fingerprints, проверяет pipeline revision и запускает immutable dataset build + REG.RU S3 publication. Текущая версия меняется только при успешной публикации; при ошибке должно остаться прежнее рабочее издание.
7. **Исправление 04.11:** если map-builder отклонил/не выполнил публикацию, он повторно помечает dirty в Redis. Раньше dirty-key удалялся при постановке задачи и после провалившейся сборки мог быть навсегда потерян до следующего изменения лота или GEO.

### Критерии фактической проверки на Home после безопасного CI

- `docker ps` + `celery inspect active` + `inspect scheduled`: ingestion/map/geocoding workers работают и используют точный deployed SHA; beat singleton, нет двойных расписаний.
- Журнал `LotSyncRun` с `triggered_by=celery-beat`, `trigger_type=scheduled_full` в двух окнах 06:00 и 18:00 UTC, `LotSyncSourceRun` с 5 прежними source IDs минус paused, и при будущем допуске accepted ETP.
- Выход `scheduled_etp_readonly_audit_task` с 21 раздельными статусами, attempt/checkpoint/errors, **никаких** database writes, `Lot-online` не дублируется.
- Сверка `LotSyncRun.result_json.map_dataset_build`, Redis dirty-key, Celery map queue и последнего `MapDataset` before/after; соответствие `published_at` времени последних исходных изменений. Проверить public REG.RU S3 manifest того же `version`, `pipeline_revision`, `point_count`, `tile_count`.
- Раздельно проверить 30 фактических объектов: source/URL, цену, статус, кадастр, provenance GEO и реальную точку `sterdez.online/map`; отсутствие скрытой подмены региона, слияния разных продаж, исчезновения фильтров источников.
- При недоступном Docker, отсутствии Celery beat, dead lease, TLS/429 или ошибке S3 — **fail**, сохранить старую карту, не утверждать «все лоты обновлены».

## Следующие работы для реального требования «все 22 — полный sync каждые 09:00 и 21:00»

- S3 acceptance **каждой** direct ETP: terminal page, два повторных полных read-only прохода, real lot detail IDs и prices, error/diff classification; добиваться S4 только после bounded production import.
- Разделить отдельную manual `etp_all_sources_campaign` и обычный двухразовый scheduler на per-source approved subset; нельзя просто перенести 19 runnable в `ETP_ACCEPTED_SOURCE_IDS`.
- Отключить или ограничить глобальную `reconcile_cross_source_duplicates` и `_expire_elapsed_auctions` после частичной кампании, пока не доказано отсутствие побочных эффектов на чужие продажи.
- Проверить, что production write imports происходят только для ETP с обязательным `detail_enrichment` и фактическим valid per-lot identity; M-ETS/CDTRF использовать специализированные source contracts, не generic fallback.
- Добавить production performance budget: 21 полный источник может содержать тысячи страниц каждый, а single Celery full task сейчас имеет soft/hard ceiling 4/5 часов. Без source-specific partitioning/leases нельзя обещать завершение 22 источников за 12-часовой интервал.
- Подтвердить 9/21 фактическими журналами **двух последовательных дней**, вместе с новым MapDataset / S3 / public WEB, а не только зелёным unit CI.
