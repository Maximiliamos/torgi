# BankrotAI product roadmap

> **Current release checkpoint (2026-10-08):** v0.3.4 is published at `f504d040`; v0.3.5 was published after exact-SHA acceptance at `260e6420`. The older P0–P12 baseline at `d290e038` is historical evidence, not proof that all newly reported map defects are fixed. P13–P17 remain an additional product-integrity workstream. The BAT backlog below is distinct.



> **Naming note (October 2026):** the production hardening sequence referred to operationally as **P0–P12** is separate from the older BAT roadmap section labels below. The BAT identifiers (`BAT-001`, `BAT-101`, etc.) are the authoritative identifiers for the long-term backlog. A green operational P0–P12 acceptance does not imply that every long-term BAT item has been implemented.


Roadmap оформлен как набор будущих GitHub issues. Каждый пункт имеет проверяемый результат; интеграции, требующие договора или внешних credentials, не считаются завершёнными только по наличию заглушки.

## Delivery phases — September 2026

| Phase | Goal | Status | Production evidence |
| --- | --- | --- | --- |
| Phase 1 | Initial lot/GEO/map delivery (legacy scope) | **Legacy acceptance only** | Initial production checks passed, but P13–P17 owner-reported correctness defects remain |
| Phase 2 Lite | Fast UX for 1–4 users without enterprise scaling | **100% complete** | Concurrency/cache/map hot-path changes verified in production |
| Phase 3 Lite | Backups, restore proof, health/recovery and durable reconciliation | **100% complete** | Full reconciliation `36239355328` succeeded; post-reconciliation Phase 3 health/recovery succeeded |
| Phase 4 Lite | Operations docs, release/recovery checklist, dependency security and issue cleanup | **100% complete** | Runbook/security gates shipped; legacy alert backlog reduced from 405 to 0; production recovery cycle green |
| Phase 5 | Final UX polish and user acceptance | **100% complete** | Registry/search/map UX shipped; production Public WEB and Functional checks green; see `docs/phase5-ux-acceptance.md` |

The delivery phases above are the practical production plan for the current deployment
(up to four users). The BAT roadmap below remains the longer-term product backlog and
must not be treated as a requirement to add enterprise infrastructure before it is needed.


## P12.5–P17 — актуальное качество продукта (требует новой приёмки)

**P12.5 — Repository Sync Cleanup:** сверить README, D: 48h/3 verified backup generations,
архивировать устаревшие Neon/Cloudflare документы, обозначить исторические отчёты,
защитить legacy tunnel и сохранить один канонический runbook. PR документации — отдельно
от P13–P17, без затрагивания рабочей БД.

**P13 — Public Map Integrity:** публичная карта содержит только активную продажу
недвижимости, не аренду, транспорт, закрытые/отменённые или неуверенно
геокодированные объекты. Подтверждение на реальной базе и пяти примерах обязательно.

**P14 — Source Lifecycle Integrity:** нормализация статусов `torgi-russia.ru`,
актуальность SourceLot→CanonicalLot, проверка свежести каждого реально
подключённого источника и изоляция исторического TBankrot. Отключённый источник
не может считаться источником свежих лотов. См. SRC-001 #931.

**P15 — Strict Cadastral GEO:** точное совпадение кадастрового номера в NSPD/PKK,
никакого `features[0]` при отсутствии соответствия, запрет публиковать
приблизительный адресный centroid как подтверждённое положение участка.

**P16 — Historical Data Repair:** сначала read-only dry-run и таблица причин,
затем backup/restore proof, ограниченная проверяемая коррекция состояний лотов
без физического удаления и bounded пересчёт старых GEO; только потом новый MapDataset.

**P17 — Production Integrity Gates:** одинаковый exact SHA Home→REG.RU→Cloudflare
для push/manual/rerun, метрики загрязнения публичной карты и запрет публикации
плохого набора данных. Приёмка 5 реальных ошибок из `Ошибки(1).docx` и
репрезентативной выборки новых лотов через DB→MapDataset→S3→public WEB.

**Статус на 2026-10-08:** указанные P13–P17 ещё не приняты как production.
Наличие Draft PR #932 не равнозначно исправлению реальных меток до релиза
и повторной пострелизной проверки. Прежние 100% для Phase 1–5 не являются
гарантией выполнения P13–P17.

## P0 — надёжное ядро торгов

### BAT-001 — Connector SDK and registry — foundation delivered

- единый async-контракт `search/fetch_lot/fetch_documents/fetch_events/healthcheck`;
- capability declaration, cursor и metadata;
- адаптеры ГИС «Торги» и TBankrot без дублирования существующей логики;
- fixture/contract tests для каждого нового источника.

Done: SDK, registry and first adapters exist. Remaining: move source-specific implementation out of `scrapers.py` incrementally.

### BAT-002 — Canonical and source identity — foundation delivered

- `CanonicalLot -> SourceLot[]`;
- уникальность `(source_system, external_id)`;
- консервативное объединение по кадастровому номеру, ЕФРСБ или делу+процедуре;
- ручное merge/split с audit log до автоматического fuzzy matching.

Done: schema, migration and ingest synchronization. Remaining: reviewed merge/split UI and stronger identity evidence.

### BAT-003 — Auction procedure fields — foundation delivered

- ЭТП, процедура, извещение, сообщение ЕФРСБ;
- должник, организатор, арбитражный управляющий, дело;
- задаток, сроки, аукцион, шаг, публичное предложение;
- фильтры и deadline notifications.

Done: normalized indexed storage and detail API. Remaining: filters and notification jobs.

### BAT-004 — Immutable document history — foundation delivered

- document identity per source lot;
- SHA-256 versions and storage keys;
- comparison pipeline for changed requisites, deadlines, asset composition and contract terms;
- malware scanning and S3/MinIO lifecycle policy before production uploads.

Done: schema and idempotent version recorder. Remaining: download workers, object storage and semantic diff.

### BAT-005 — Official EFRSB Publications API — integration-ready

- operator-issued credentials only;
- `/v1/auth`, trade message search/detail and authorized archive URL;
- rate limiting, retries, fixtures and production contract monitoring;
- no CAPTCHA bypass or browser scraping.

Done: authenticated connector contract. Blocked for live acceptance: production service agreement and credentials.

## P1 — investment decision and participation

### BAT-101 — Maximum bid calculator — delivered API foundation

- explicit conservative sale price;
- repair, legal, holding, tax, commission, capital, target profit and risk reserve;
- pessimistic/base/optimistic scenarios;
- maximum bid, profit, ROI, annualized return and break-even.

Done: desktop/web form and saved assumptions. Remaining: approval history.

### BAT-102 — Participation control — delivered data/API foundation

- ETP accreditation and signature validity;
- application, deposit purpose/sending/receipt, signed documents and acceptance;
- countdowns from normalized procedure deadlines;
- discrepancy warnings between notice and documents.

Done: desktop/web checklist UI. Remaining: notifications and document-to-field reconciliation.

### BAT-103 — Evidence-based valuation

- comparable dataset with distance, price/m², date, exposure and condition;
- separate liquidity, expense and risk models;
- LLM only explains precomputed values;
- no price output when evidence policy is not met.

## P2 — Deal Room and learning loop

### BAT-201 — Organizations, roles and partners

- tenant-scoped users and RBAC;
- ownership, assignments, approvals and audit trail;
- planned/actual partner contributions and profit allocation.

### BAT-202 — Outcome dataset

- recommendation -> bid -> purchase -> expenses -> sale -> actual profit;
- immutable model/prompt/data provenance;
- backtesting, calibration and drift monitoring.

### BAT-203 — Operations

- PostgreSQL primary production database;
- S3/MinIO documents, encrypted backups and restore drills;
- connector health dashboard, metrics, alerts and runbooks;
- owner decision and publication of a root `LICENSE`.

## P0 — завершение безопасного web/desktop parity

### BAT-301 — Three-source bulk synchronization

- единая Celery orchestration для ГИС «Торги», TBankrot и РАД/ЛОТ-ОНЛАЙН;
- idempotency key, per-source cursor/progress/retry/cancel и частичный результат;
- web показывает недоступные источники и не объявляет неполный проход успешным;
- acceptance: повторный запуск не создаёт дублей, progress восстанавливается после restart worker.

### BAT-302 — Background AI and GEO operations

- queue endpoints для single/batch AI, GEO, retry failed GEO и re-geocode;
- server-side concurrency/rate limits, budget limit и audit actor;
- acceptance: browser disconnect не останавливает job, статус доступен после повторного входа.

### BAT-303 — Real RBAC and tenant isolation

- `reader` только читает, `analyst` меняет личный workflow, `admin` запускает jobs и merge/split;
- tenant/user scope для watchlist, notes, scenarios, checklist, audit log;
- отрицательные API-тесты для каждого endpoint и роли.

### BAT-304 — Honest deployment modes

- заменить двусмысленный `API_READ_ONLY` на curated/read-only режимы;
- отдельные allowlists методов и route capabilities endpoint для UI;
- скрывать недоступные кнопки, возвращать `403` для запрещённого действия.

## P1 — оставшиеся desktop-возможности в web

### BAT-305 — Import/export jobs

- безопасная загрузка HTML с size/type limits и malware scan;
- Excel export как streaming/background download с теми же колонками и фильтрами;
- retention и удаление временных файлов по политике.

### BAT-306 — Map parity

- Yandex provider как optional web layer без silent fallback после успешной загрузки;
- кадастровый WMS/proxy, marker clustering и серверная фильтрация цены/status/review;
- viewport pagination вместо фиксированного лимита 5000 точек.

Done 2026-08-05: web переведён с Leaflet на Яндекс.Карты, перенесены кластеризация,
кадастровая геометрия, desktop-маркеры и смена статуса «светофором». Remaining:
серверная viewport pagination и production API key/domain policy Яндекс.Карт.

Follow-up 2026-08-05: web повторяет desktop-композицию карты в двух состояниях —
панель кадастрового поиска/фильтров и карточка выбранного лота с фото, ссылками,
процедурой и оценкой. Map feature вынесен из `main.tsx`; `allow-same-origin`
удалён, сообщения защищены проверкой окна, opaque origin и уникального channel id.

Follow-up 2026-08-05: устранены два дефекта внешнего ревью. Совпадающие координаты
теперь показываются счётчиком и раскрываемым списком лотов; серверные группы дублей
передают полный список исходных публикаций. Iframe и экземпляр Яндекс.Карт стали
долгоживущими, поэтому открытие карточки и изменение оценки сохраняют viewport.
Ручное объединение/разделение групп защищено ролью `admin`. Это покрыто API- и
Playwright-регрессиями; remaining BAT-306 не изменился: viewport pagination и
production API key/domain policy.

### BAT-307 — Web parity acceptance suite

- Playwright journeys: search/import, registry/detail, watchlist, map/review,
  calculator/history, participation, documents diff и duplicate review;
- contract matrix «desktop action → API → web screen → role → automated test»;
- parity считается готовым только при зелёной матрице, а не по наличию кнопки.

Done 2026-08-05: базовый Playwright journey проверяет вход, реестр, поиск,
ручной/списочный регион, фиксированную категорию, Яндекс.Карту, сделку и диагностику.
Карта дополнительно проверяется на трёх лотах с одинаковыми координатами,
индивидуальный выбор, неизменность `contentWindow`, центра и масштаба после выбора
и изменения «светофора».
Remaining: сценарии импорта и изменения данных для каждого workflow и роли.

## P1 — аудит и эксплуатация

### BAT-308 — Split monoliths

- разнести GUI по feature widgets/controllers, API по routers/services, scraper implementations по connectors;
- не допускать новых модулей свыше 1000 строк без ADR.

### BAT-309 — Observability and recovery

- SLO, Prometheus/OpenTelemetry, queue lag/source freshness/GEO/AI error alerts;
- PostgreSQL encrypted backups, quarterly restore drill и документированный RPO/RTO;
- release rollback rehearsal и migration compatibility window.

### BAT-310 — Supply chain and governance

- root `LICENSE`, SBOM, signed release images, dependency update policy;
- branch protection, required checks, secret scanning/private vulnerability reporting.

## P2 — качество интерфейса и данных

### BAT-311 — Frontend architecture and accessibility

- feature modules/routes, schema-derived labels, i18n и единый design system;
- WCAG 2.2 AA keyboard/focus/contrast tests.

### BAT-312 — Outcome and valuation evidence

- comparable evidence policy и отказ от оценки при недостаточных данных;
- полный outcome dataset, backtesting, calibration и drift monitoring.
