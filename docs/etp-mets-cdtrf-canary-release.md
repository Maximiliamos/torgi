# Controlled canary for M-ETS and CDTRF — release gate

**Release branch:** `release/etp-mets-canary-20261029`. No source is enabled by default. All existing regional and nationwide source specifications remain unchanged.

## API surface (administrator session required)

- `GET /api/etp/pilots/m-ets.ru/preview`: one M-ETS public property detail, read-only.
- `GET /api/etp/pilots/cdtrf.ru/preview?page=1`: up to 20 official catalogue rows, safely reduced to property-category candidates, `trade_id` and **independent** `trade_lot_id`; does not expose a debtor name as a property title.
- `GET /api/etp/pilots/sources`: directory of 14 additional direct-ETP read-only pilots, all disabled for production sync.
- `GET /api/etp/pilots/nistp.ru/preview?limit=5` (replace `nistp.ru` with any registered source): bounded candidate extraction. A candidate is not an approved property lot and must not be published to the map.
- `POST /api/etp/pilots/m-ets.ru/import/243003-1`: **disabled** unless the home origin explicitly sets `ETP_METS_CANARY_IMPORT_ENABLED=1`. Validates exact original card identity, cadastral, price, status and property classification.
- `POST /api/etp/pilots/cdtrf.ru/import/390364/390899`: **disabled** unless `ETP_CDTRF_CANARY_IMPORT_ENABLED=1`. Revalidates trade ID and lot ID in the original HTML/Nuxt JSON and checks price and cadastral fields.

The ID examples were valid for the 28–29 October **sample snapshots only**. Always verify that the selected lot is still publicly accessible and applicable before importing.

## What happens when an import is deliberately enabled

The standard `persist_lot` path creates or updates `SourceLot` / `ProcessedLot` and records an audit action. A same-cadastre record from a different source returns **409: manual review required** rather than silently merging potentially different sales. No missing-lot reconciliation or source archiving runs. The lot may lack approved geo coordinates: **do not expect a map marker before the existing geo worker and map publication pipeline has processed and published it**.

## Production gate (not automatic)

1. Review all changed files and green CI (SQLite, PostgreSQL, type checking, security audit, WEB).
2. Check admin authentication and preview responses on the actual home origin, including a rate-limited/blocked source.
3. Enable **only one** source's canary import toggle for a bounded validation window. Import a single independently confirmed real-estate lot.
4. Verify `SourceLot` and `ProcessedLot` identity, category, status, prices, audit log, duplicate status, geo queue/coordinates, dirty map dataset, published tile bundle and source filter on `sterdez.online`.
5. Check same-ID idempotency; attempt an already-known cadastral duplicate and confirm 409.
6. Restore source toggle to 0; retain rollback evidence and monitor consecutive runs before scheduling the source.

**Never** set automatic sync, reconcile missing, archival, or release flags based only on the two browser capture pages. The pilot code is separate from the unresolved 20-ETP Draft PR #965.

## Current acceptance status

- Source adapter code + offline tests have been authored.
- Production rollout, live previews, actual saved data and map publication are **not certified** until the above checks are observed on the specific release SHA.
- All legacy sources remain configured as before.
