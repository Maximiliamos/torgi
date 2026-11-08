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
1. The weak count/id-sum/max-timestamp fingerprint was replaced by an exact streaming hash of map-relevant fields. PostgreSQL promotion now locks `processed_lots` and task/run state tables before its final fingerprint and atomic switch. A queued ingestion job that has not yet registered a `LotSyncRun` remains an admission race; for a full guarantee, its scheduler must acquire a shared publication/start fence when enqueueing.
2. A completed `LotSyncRun` does not, by itself, prove all seven source-run contracts passed. Check per-source completeness and explicit run success before enabling automatic publication.
3. A historical failed GEO task is not active, so the current guard does not reject it. Publication must use a run-scoped mandatory GEO completion result if failure is meant to block this particular map.
4. The existing builder filters canonical and non-archived rows and coordinate bounds, plus region sanity. It **does not explicitly filter auction status or real-estate category in its own SELECT**. Confirm upstream canonical eligibility and update membership semantics with tests and revision bump before enabling the new publication flow.
5. Both legacy and regional S3 manifest uploads now sign `If-None-Match: *` and fail on 412, preventing overwrite of an existing manifest. Object-store provider support and end-to-end integrity (complete tile SHA inventory / Object Lock) remain unverified; do not promote until staging compatibility has been validated.
6. Redis lock has one-hour TTL. Long GEO runs may outlive lease; verify renewal strategy. Stale queued tasks may also block publication until recovered.

## Reproducible profile plan (permission-free local fixtures)
Warm and cold runs should report: p50/p95 provider request latency, cache hits, retries, resolved/failed work items, resolver wall time, DB save/chunk time, waiting for GEO lease, map select/render/tile commit time, S3 upload and manifest verification separately, and end-to-end critical path. Compare sequential `ingestion -> GEO -> map` with GEO overlap on a **disposable database only**. Hold point count, cache temperature, worker count, region mix and S3 mock latency constant. `pytest -q tests/test_map_publication_fence.py` exercises synthetic blockers, concurrent source changes, old-dataset preservation, and retry.

**Additional implementation:** exact source hash, PostgreSQL promotion table locks, explicit active ingestion/GEO blockers, conditional-create manifest for both S3 layouts, and regression tests including PostgreSQL writer contention.

**Instrumentation added in this branch:** GEO now reports `selection_duration_ms`, `resolve_duration_ms`, and `save_duration_ms`; Map reports `selection_duration_ms`, `tile_duration_ms`, `object_store_duration_ms`, and `promotion_duration_ms` in addition to its existing totals. These are phase timers, not benchmark observations.

**Measurements:** no executable repository/DB snapshot or production metric exports were available to this review. Consequently no empirical GEO or Map time deltas are claimed. Existing `duration_seconds`, `build_duration_ms` and `duration_ms` are instrumentation surfaces, not a benchmark result.

## Release gate and ownership boundary
Draft only. No production execution. The remaining key dependency is an **ingestion/GEO coordinator interface** (owned by the source orchestrator): `PublicationProof(sync_run_id, expected_source_ids, source_end_evidence, geo_task_id, geo_success, finished_at)`. The existing `LotSyncSourceRun.complete_source_run` is intentionally false for some non-reconciling ETP sources, even after successful end-page verification; it therefore cannot alone prove seven-source completeness. Until that interface is provided, automatic MapDataset promotion after overlapping ingestion/GEO must remain disabled. This PR does not modify `ingestion.py` or workflows to fake source-end proof.

Confirm category/status map membership semantics with canonical policy owners before changing `MAP_DATASET_REVISION`; historic closed lots are currently rendered with black pins. Request an independent review of publication lock order and REG.RU conditional PUT support. Report green *exact-head* CI, staging manifest tests and measured GEO/Map latency before merging.
