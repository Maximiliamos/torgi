# Changelog

All notable changes are documented here. The project follows Semantic Versioning
for tagged releases.

## Unreleased

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

- Added RAD-aware Russian address normalization and district-safe Nominatim matching.
- Added bounded parallel bulk GEO processing with accurate success/failure counts.
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
