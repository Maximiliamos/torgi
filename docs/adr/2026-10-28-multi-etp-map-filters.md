# ADR: multi-source filtered map, source provenance and API module ratchet

Date: 2026-10-28 · Status: accepted for isolated map-filter release; ETP pilots remain Draft #965

## Context

Public REG.RU S3 map bundles are immutable and optimized for unfiltered maps.
Filter requests (region, starting price) already use a bounded runtime index.
A client-only ETP selector would be incorrect because low-zoom clusters are
preaggregated and deduplicated canonical assets appear under multiple sources.
New ETP pilot profiles are not part of this release and must not enter production ingestion implicitly.

## Decision

- GET /api/map/sources exposes five existing sources plus 22 pilot ETP options; pilot is
  separate from "has_database_records". No synthetic source availability.
- A bounded, validated `sources` comma-separated query selects **any** of the
  requested source systems, including ProcessedLot children merged into a
  canonical ProcessedLot. SQL EXISTS handles viewport and statistics; runtime
  tile index preloads duplicate-group membership once per published version.
- Filtered requests go through the authenticated API and have source-inclusive
  cache keys and tile-cluster IDs. Without filters, the S3 hot path remains
  unchanged. Changing selected sources resets direct ObjectManager tiles and
  prevents stale marker reuse.
- No write, schedule, cross-site fetch, archival, geocoding or migration is
  introduced by the filter itself. New source extraction stays behind
  read-only pilots until pagination, deduplication and completeness gates pass.
- Source catalog labels are static audited ETP IDs; actual DB presence is
  derived from `processed_lots`. A pilot can be selected and correctly show
  no markers until it is ingested, with an explicit UI indicator.

## Complexity / safety review

`WEB/src/features/map/MapFiltersPanel.tsx` was extracted from the map
monolith. MapView shrinks below its existing 2038-line ratchet. New source
matching lives in `map_source_filters.py`, while runtime provenance fields
live in `map_runtime.py`. The FastAPI endpoint bindings add 27 lines to
the grandfathered `api.py` (2538 → 2565); we explicitly raise the narrowly
reviewed ratchet to **2565**, without granting unbounded expansion. Next API
maintenance should extract map routes into an APIRouter and lower the ratchet.

## Acceptance / rollback

Test input validation, cache-key separation, OR semantics, duplicate-group
matching, empty-pilot selection, full tile rendering, map source options,
registry and operations journal startup, schema migrations and Docker smoke.
Release only after all blocking CI/audits and a safe staged production rollout.
Rollback = keep or restore the existing published map dataset / home API commit;
new source records are never auto-archived by the filter.
