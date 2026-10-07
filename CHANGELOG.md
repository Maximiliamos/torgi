# История изменений

## 0.3.2 — planned next accepted release

Remote-only hardening prepared after v0.3.1:

- Incrementally extract source lot filtering, map geometry, HTTP request models and desktop preview utilities without changing their public contracts.
- Sign and independently verify release SBOM/checksum evidence with GitHub OIDC/Sigstore before publishing a release.
- Stage an opt-in, encrypted off-host PostgreSQL backup exporter (not activated without owner credentials and restore evidence).
- Add a read-only branch disposition inventory workflow; preserve unreviewed unique commits.
- Keep P0–P12 production and release gates unchanged; release only on the final accepted main SHA.

## 0.3.1 — 2026-10-07

Safe post-closeout hardening.

- Retain the latest three verified PostgreSQL backup generations on the Home host.
- Prevent new >1000-line Python/TypeScript/JavaScript modules without explicit grandfathering.
- Pin GitHub Actions and production container images to immutable revisions/digests.
- Keep P0–P12 production acceptance and fail-closed backup verification unchanged.


## 0.3.0 — 2026-10-07

Repository closeout release after the final production acceptance baseline.

- Production P1/P11, public WEB smoke and functional reliability are green on the accepted production line.
- Formalized one product release version across Python, WEB and Windows installer metadata.
- Added repository consistency checks, dependency governance, CODEOWNERS, contribution/PR templates and automated branch hygiene.
- Added accepted-release automation with SBOM/checksum evidence.
- Retired legacy Neon maintenance and one-time REG.RU password bootstrap workflows from active Actions.
- Separated per-release acceptance from periodic disaster-recovery certification.


## October 2026 — production hardening and cadastral search

- completed the operational P0–P12 hardening sequence for durable reconciliation, data-quality controls, maintenance/DR, operations UX, source/GEO resilience and final acceptance tooling;
- moved canonical production PostgreSQL backups to `D:\BankrotAI\dr-backups` with an approximately 48-hour cadence, checksum + isolated restore verification and one latest verified retained copy;
- bounded MapDataset retention and recovery so cleanup is restartable and never requires Docker volume pruning;
- added source/provider circuit breakers, bounded retries, Home runner/VPN diagnostics and controlled GEO backlog draining;
- added P9 GEO quality scoring/canary and P10 operations dashboard;
- added P11 evidence-based production acceptance workflow;
- added P12 standalone cadastral search with persistent cache, NSPD/WMS/Photon/IK12 bounded fallbacks, map geometry/bounds and production acceptance coverage;
- hardened Home Docker deployment, exact-SHA rollout and REG.RU/Cloudflare production gates.

## 0.2.0 — 2026-08-01

- исправлены поиск по всей России и сброс фильтров карты;
- физическое удаление заменено архивированием с сохранением истории;
- добавлены проверяемые резервные копии, восстановление и ежедневная ротация;
- добавлены состояние источников, метрики полноты, очередь GEO и retention raw-ответов;
- добавлены ручные merge/split дублей с журналом решений;
- навигация перестроена вокруг поиска, реестра, карты и сделки;
- добавлены сценарии максимальной ставки, контроль участия, watchlist, заметки и фильтры;
- добавлены версии документов, сравнение и экспорт диагностики;
- EXE получил версию, иконку, packaged smoke-test и сценарий установщика.

## Предыдущие незавершённые изменения

### Auction operating model

- Added public RAD / LOT-ONLINE catalogue search with category, Yaroslavl region,
  active/archive mode, pagination, desktop import, and connector-registry support.
- Added connector SDK and registry adapters for Torgi.gov.ru, TBankrot and the official authenticated EFRSB Publications API.
- Added canonical/source lot identity, normalized auction procedure fields and participation checklists.
- Added immutable SHA-256 document version records.
- Added a transparent three-scenario maximum-bid calculator and API endpoints.

### Security

- Enforced fail-closed API authentication for production, including GET requests.
- Added authenticated WEB ingress, non-default required Compose secrets, Redis
  authentication, distributed rate limiting, and DB/Redis readiness checks.
- Prevented AI API keys and other secrets from being stored in `app_settings`.
- Kept TLS verification mandatory in production.

### Reliability

- Fixed blank desktop maps caused by oversized `QWebEngine.setHtml` payloads:
  map shells now load first and lot data streams in bounded batches.
- Bundled Leaflet and marker-cluster assets with the desktop application and added
  a Leaflet fallback when the Yandex Maps API is unavailable through a VPN/proxy.
- Reworked GIS Torgi and TBankrot desktop searches around verified real-estate
  categories and excluded rentals and non-real-estate assets.
- Added nationwide real-estate search across GIS Torgi, TBankrot and RAD / LOT-ONLINE.
- Added cross-source duplicate recognition with one registry/map card and aggregated
  source links and photographs.
- Added RAD-aware Russian address normalization and district-safe Nominatim matching.
- Added LOT-ONLINE detail-card enrichment for listing cards without a usable address
  or cadastral number, including `Address (location)` label support.
- Excluded partial ownership shares, cultural-heritage objects and ensembles from
  search, registry geocoding and map markers.
- Made nationwide source failures visible as an explicit incomplete-search warning
  instead of presenting a partial database as a complete Russia-wide result.
- Added synchronized map filters for minimum/maximum price and region.
- Added black markers for completed, cancelled and archived auctions, including
  automatic recoloring when a known auction end time passes.
- Restricted Leaflet and Yandex zoom/panning to one non-repeating world copy.
- Restored the original unrestricted Yandex Maps viewport and removed automatic
  post-load replacement with Leaflet; one-world restrictions remain Leaflet-only.
- Added bounded parallel bulk GEO processing with accurate success/failure counts.
- Added PKK/NSPD connectivity circuit breakers so a VPN-blocked cadastral service
  does not impose the same timeout on every lot in a bulk queue.
- Added incremental Leaflet/Yandex marker updates as each GEO result is committed.
- Added map lot previews with source photos, auction details, external source links,
  and persisted interested/unsure/rejected review actions.
- Added RAD / LOT-ONLINE listing image extraction for map previews.
- Prioritized recent GEO snapshots and increased the interactive map limit to 5,000 lots.
- Scoped external lot identifiers by source with an Alembic migration.
- Removed destructive closed-lot cleanup and retained status history.
- Moved bulk synchronization to observable Celery tasks with explicit queue errors.
- Added strict AI response validation, provider/model provenance, and human-review
  requirements.
- Replaced ambiguous max-number extraction with provenance-bearing price and area
  extraction results.

### Build and quality

- Added reproducible runtime and development lock files.
- Added SQLite, PostgreSQL, migration, WEB, Playwright, and Docker CI coverage.
- Split AI and scraper contracts into dedicated modules.
