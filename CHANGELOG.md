# Changelog

All notable changes are documented here. The project follows Semantic Versioning
for tagged releases.

## Unreleased

### Security

- Enforced fail-closed API authentication for production, including GET requests.
- Added authenticated WEB ingress, non-default required Compose secrets, Redis
  authentication, distributed rate limiting, and DB/Redis readiness checks.
- Prevented AI API keys and other secrets from being stored in `app_settings`.
- Kept TLS verification mandatory in production.

### Reliability

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
