# Bounded mixed-source ingestion audit

## Implemented scope
- `src/bankrotai/services/ingestion.py`: mixed reconcile/non-reconcile source runs use bounded concurrency (default 3, constructor range 1–7).
- `src/bankrotai/services/ingestion_concurrency.py`: scheduling, unexpected-error isolation, and the per-run reconciliation lock are extracted from the already-grandfathered ingestion module.
- Missing-lot reconciliation remains fail-closed and is now atomic per source: no intermediate archival commits are made.
- Per-source checkpoints, heartbeat/lease renewal, coverage guards, quarantine gates, and the two-complete-runs archival threshold remain in force.
- Concurrent stall detection uses the oldest running-source progress timestamp, so a healthy peer cannot mask a stalled source.
- Duplicate source IDs in one run are rejected because the durable checkpoint key is `(sync_run_id, source_system)`.
- No connectors, database schema, GEO/Map code, S3 mutation path, or production workflow is changed.

## Concurrency safety
Source scans may overlap, but synchronous persistence still runs on the event-loop thread and therefore does not create seven simultaneous SQL writer threads. Reconciliation critical sections are serialized within the nationwide run. Result ordering follows the input specs.

Unexpected exceptions that escape source setup/checkpoint code are converted to a failed `SourceSyncResult` so sibling source tasks continue. Normal source/coverage/quarantine failures still use the existing fail-closed path inside `_sync_source`.

Missing-lot archival previously committed every 250 rows. That allowed a mid-reconciliation exception to leave a failed source with partially durable archival. The PR removes those intermediate commits; the source reconciliation transaction commits only after every missing row and canonical promotion succeeds. Progress callbacks still renew the durable run/checkpoint state.

## Added regression coverage
`tests/test_ingestion_mixed_concurrency.py` covers:
- mixed reconcile/non-reconcile scheduling with a bounded peak;
- deterministic seven-source result coverage/order;
- unexpected source failure isolation;
- duplicate source rejection before work starts;
- serialized reconciliation critical sections;
- repeated runs without ContextVar leakage;
- invalid concurrency limits.

`tests/test_persistent_ingestion.py` adds DB-backed cases for:
- one stale source not being hidden by a fresh concurrent peer;
- mixed source coverage collapse remaining fail-closed while a non-reconciling sibling succeeds;
- rollback of every missing/archive change when reconciliation fails after archival has started.

The pre-existing ingestion suite continues to cover durable leases, heartbeat renewal, source-run checkpoints, two-run archival, failed-source non-archival, regional scope, cardinality collapse, quarantine and source-end proof.

## Reproducible validation
Targeted offline:
```bash
pytest -q tests/test_ingestion_mixed_concurrency.py
pytest -q tests/test_persistent_ingestion.py -k \
  "stall_detection_is_not_masked_by_fresh_concurrent_source or \
   mixed_source_coverage_failure_is_isolated_and_never_archives or \
   reconciliation_failure_rolls_back_all_missing_archive_changes"
```

Full offline and static gates:
```bash
pytest -m "not postgres"
python scripts/check_module_sizes.py
ruff check src tests
mypy src/bankrotai
```

Disposable PostgreSQL integration:
```bash
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/bankrotai_test \
  pytest -m postgres
```

Synthetic scheduler benchmark:
```bash
PYTHONPATH=src python benchmarks/ingestion_source_scheduler.py
```

The scheduler benchmark contains no DB, network, GEO, S3 or MapDataset work. A local five-sample reproduction measured serial median about 0.7022 s and bounded(3) median about 0.2415 s, approximately 2.91x for the synthetic delays. It is not a production speed claim.

## Coordination with adjacent work
At review time the adjacent PRs do not overlap this PR's runtime files:
- #1139 preflight/checkpoint recovery: scripts/tests/docs only.
- #1140 GEO/Map publication fence: `geo_backfill.py`, `map_builder.py`.
- #1142 M-ETS/CDTRF detail concurrency: connector/detail transport files.
- #1143 detail freshness: `batch_persistence.py` and a new freshness helper.
- #1144 seven-source performance baseline: benchmark/docs (and its own CI edits).

PR #1142 can add up to two detail workers inside M-ETS/CDTRF. Source-level concurrency does not duplicate a source ID, and those two sources use distinct provider hosts, but a future global per-host limiter would require a shared transport-level interface rather than another ingestion lock.

## Remaining risks
1. Atomic missing-lot reconciliation trades partial-commit risk for a longer per-source transaction. On large missing sets, PostgreSQL lock/transaction duration must be observed in a staging or disposable DB benchmark before production rollout.
2. The source scheduler intentionally does not make synchronous persistence multi-threaded; measured end-to-end speedup will therefore be lower than the scheduler-only 2.91x when DB work dominates.
3. Provider-side concurrency in adjacent connector work is independently bounded. If a provider later shares hosts across logical source IDs, rate limiting must move to a shared host-level transport service.
4. Independent human/agent review is still required before marking the draft ready; this PR must not be merged solely on author verification.

No production ingestion, GEO drain, S3 mutation or MapDataset promotion is part of this validation.
