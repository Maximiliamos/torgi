# GEO / Map publication profiling and safety (read-only review)

## Inspected baseline (main, 2026-11-08)
- `geo_backfill.py`: bounded selection (at most 1,000), query-key deduplication, 30-day success cache, `geo_max_workers` thread pool for external requests; persistence in chunks of 100, with isolated retry on chunk errors. Redis serialization lock for PostgreSQL workloads. Emits `duration_seconds`, `lots_per_second`, `cache_hits`, `processed`, `failed`.
- `map_builder.py`: selects canonical non-archived coordinate rows, builds each zoom level in Python, commits every 500 tiles, validates overall and source/region coverage and atomically changes `is_current` in a PostgreSQL advisory-locked transaction. Emits `build_duration_ms` and `duration_ms`.
- `map_object_store.py`: pages 250 tiles and uploads using bounded workers; publishes manifest *after* tile upload and checks public accessibility, version and revision. Contains timeout/retry backoff; upload time should be separately recorded.
- `map_dataset_version.py`: explicit `r6` pipeline revision. Do not change revision without changing membership/payload semantics.
- PR #1137 already addresses old-MapTile deletion by materializing bounded PK batches. This change **does not copy that retention fix**.

## Safety change in this PR
A map candidate captures `map_source_fingerprint` before rendering. It is checked before S3 upload and again inside the atomic promotion transaction. In-progress `LotSyncRun` or GEO background tasks block promotion. A rejected candidate never changes the previous current dataset; a retry must create a fresh version.

### Important limitations (must be resolved before release)
1. Fingerprint is **not a transactionally consistent write fence**. It uses count/ID sum/max update timestamps, so a concurrent update to a non-max row could escape detection if timestamp semantics are violated. The read of blockers and promotion still races with new ingestion transactions unless both take a shared DB advisory protocol.
2. A completed `LotSyncRun` does not, by itself, prove all seven source-run contracts passed. Check per-source completeness and explicit run success before enabling automatic publication.
3. A historical failed GEO task is not active, so the current guard does not reject it. Publication must use a run-scoped mandatory GEO completion result if failure is meant to block this particular map.
4. The existing builder filters canonical and non-archived rows and coordinate bounds, plus region sanity. It **does not explicitly filter auction status or real-estate category in its own SELECT**. Confirm upstream canonical eligibility and update membership semantics with tests and revision bump before enabling the new publication flow.
5. Object-store manifests use versioned keys and public GET verification; the existing upload code does not enforce conditional immutable PUT, complete tile hash inventory or S3 bucket object-lock. This change does not claim fully immutable S3 manifests.
6. Redis lock has one-hour TTL. Long GEO runs may outlive lease; verify renewal strategy. Stale queued tasks may also block publication until recovered.

## Reproducible profile plan (permission-free local fixtures)
Warm and cold runs should report: p50/p95 provider request latency, cache hits, retries, resolved/failed work items, resolver wall time, DB save/chunk time, waiting for GEO lease, map select/render/tile commit time, S3 upload and manifest verification separately, and end-to-end critical path. Compare sequential `ingestion -> GEO -> map` with GEO overlap on a **disposable database only**. Hold point count, cache temperature, worker count, region mix and S3 mock latency constant. `pytest -q tests/test_map_publication_fence.py` exercises synthetic blockers, concurrent source changes, old-dataset preservation, and retry.

**Instrumentation added in this branch:** GEO now reports `selection_duration_ms`, `resolve_duration_ms`, and `save_duration_ms`; Map reports `selection_duration_ms`, `tile_duration_ms`, `object_store_duration_ms`, and `promotion_duration_ms` in addition to its existing totals. These are phase timers, not benchmark observations.

**Measurements:** no executable repository/DB snapshot or production metric exports were available to this review. Consequently no empirical GEO or Map time deltas are claimed. Existing `duration_seconds`, `build_duration_ms` and `duration_ms` are instrumentation surfaces, not a benchmark result.

## Release gate
Draft only. Do not merge or run production until: run-scoped completion barrier, status/category eligibility tests, immutable manifest verification, cross-transaction ingestion lease coordination, and reproducible timing results have passed.
