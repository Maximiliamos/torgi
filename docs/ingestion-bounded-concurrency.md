# Bounded mixed-source ingestion: offline audit

## Scope
- `src/bankrotai/services/ingestion.py` only; no connectors, database schema or production workflow edits.
- Default concurrency: 3, configurable as `max_concurrent_sources` (1–7).
- Mixed reconcile/non-reconcile source runs now share a bounded scheduler.
- Source order is preserved in payloads; duplicate `source_id` specifications are rejected.
- Reconciliation archive critical sections are serialized per nationwide run.
- Unexpected source setup errors produce a failed source result rather than cancel sibling tasks.
- Errors in source sync or archival clear `complete_source_run`, preventing false coverage reports.
- Existing per-source checkpoint writes, source-level guard checks, and durable watchdog continue to be used.

## Risk notes / follow-up review
- Synchronous SQL commits and archive scans still block the event loop; this is NOT seven simultaneous DB writer threads. No change to transaction isolation is proposed.
- Existing lease ownership checks and watchdog are inherited, not rewritten; dedicated DB-backed integration tests of lease loss, runner restart and checkpoint recovery are still required.
- If persisting a failure checkpoint itself fails, its exception is logged but the source is represented as failed in the run result; durability under DB outages needs integration coverage.
- Neither the existing source-coverage guard nor the two-complete-runs missing-lot archival threshold is weakened.
- Do not merge until tests on PostgreSQL with all configured seven source contracts pass, including partial archival failure and repeated runs.

## Tests added
`tests/test_ingestion_mixed_concurrency.py` checks bounded mixed-source scheduling, deterministic result order, error isolation, duplicate IDs, serialized reconciliation critical section, context reset on rerun, and invalid limit values.

**Execution status:** integration/pytest suite not executed in this environment; repository cannot be cloned into the local runtime and dependency-complete test environment is unavailable. No production action was run.

## Synthetic benchmark (not actual source-run timings)
Method: seven simulated independent async waits with delays [0.12, 0.09, 0.11, 0.08, 0.13, 0.10, 0.07] seconds, five samples, serial orchestration vs three concurrent tasks. A local scheduler-only reproduction yielded:
- Serial: 0.7026, 0.7022, 0.7021, 0.7022, 0.7021 seconds; median **0.7022 s**.
- Bounded(3): 0.2418, 0.2414, 0.2415, 0.2414, 0.2414 seconds; median **0.2414 s**.
- Ratio of medians: **2.91×**.
- The committed `benchmarks/ingestion_source_scheduler.py` independently reproduces the simulation; values will vary by host.

No production source timing, network latency, DB contention, GEO or map wall time is inferred from this synthetic run.

## Validation gate before merge
```bash
PYTHONPATH=src python -m pytest -q tests/test_ingestion_mixed_concurrency.py
PYTHONPATH=src python benchmarks/ingestion_source_scheduler.py
```

Additional required pre-production tests in an isolated PostgreSQL fixture:
1. Seven real source specs: mixed reconciliation flags and source-end proofs.
2. Two complete successful runs archive only eligible missing source lots.
3. Timeout/failed page/coverage collapse/quarantined detail: no missing-lot archival.
4. Lease lost, heartbeat stalls, interrupted checkpoint, and repeat run.
5. Duplicate/canonical promotion correctness with concurrent source persistence.
6. Measured DB transaction conflicts and wall-clock baseline before/after at fixed source fixture snapshots.
