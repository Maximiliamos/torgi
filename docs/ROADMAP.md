# BankrotAI product roadmap

Roadmap оформлен как набор будущих GitHub issues. Каждый пункт имеет проверяемый результат; интеграции, требующие договора или внешних credentials, не считаются завершёнными только по наличию заглушки.

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

Remaining: desktop/web form, saved assumptions and approval history.

### BAT-102 — Participation control — delivered data/API foundation

- ETP accreditation and signature validity;
- application, deposit purpose/sending/receipt, signed documents and acceptance;
- countdowns from normalized procedure deadlines;
- discrepancy warnings between notice and documents.

Remaining: UI, notifications and document-to-field reconciliation.

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
