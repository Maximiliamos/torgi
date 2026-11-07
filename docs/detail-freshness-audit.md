# CDTRF change detection and batch persistence (read-only assessment)

## Scope and evidence

Reviewed `src/bankrotai/services/batch_persistence.py`,
`src/bankrotai/services/ingestion.py` (read only),
`src/bankrotai/connectors/registry/etp_incremental.py` (read only),
`src/bankrotai/domain.py` and models in `src/bankrotai/db.py`.

The reported CDTRF run had 1,584 updated lots out of 1,585.
**Its per-record before/after payloads were not available for this analysis**.
Consequently there is no verified attribution of those updates to a specific
field. High churn is consistent with changing transport/source fingerprints,
changed raw JSON, genuine source updates, or normalization changes. A source
run count alone cannot distinguish these explanations.

## Current code path

`NationwideIngestionService._persist_page` calls a batch function when
`use_gis_batch_persistence` is enabled (the constructor default). The name
is legacy: this path is used for accepted source pages, not just the GIS
source. The ingestion gate compares `_source_fingerprint` and
`_normalized_source_fingerprint` then calls
`persist_changed_lots_batch` for changed/new lots.

The batch implementation executes, **per non-empty chunk** (typical existing
lot case):
1. SELECT matching SourceLot links.
2. SELECT matching ProcessedLot rows.
3. UPSERT ProcessedLot projection.
4. SELECT ProcessedLot ids again.
5. UPSERT SourceLot projection.
6. Optional INSERT status history and/or price history.
New identities additionally require CanonicalLot UPSERT and SELECT.

The caller makes an initial SourceLot SELECT and one commit per page. Query
counts depend on autoflush, driver execution strategy, number of chunks,
history events and SQLAlchemy settings: the list above is a *static code-path
estimate*, not an observed metric. The new skip gate can add one set-based
SourceLot UPDATE per chunk with unchanged candidates, avoiding processed and
source upserts for those candidates. It does not eliminate the parent
ingestion lookup or transaction commit. Do not extrapolate any claimed speedup
from static statement counts.

## Semantics

`compare_source_values` compares the persisted SourceLot business columns.
It excludes observation bookkeeping and the separate source revision timestamp.
Raw evidence comparison ignores a **small explicit allowlist** of transport
noise; unexpected raw fields fail closed. Price, status, deadlines, sale terms,
deposits, legal identifiers and calendar changes cause an update. Historical
canonical grouping remains immutable in the existing upsert. The added batch
guard also requires all ProcessedLot projection fields (except its
`last_update` bookkeeping field) to agree. On a skip it still updates
`last_seen_at`, `last_sync_run_id` and
`missing_successful_runs`, preserving coverage tracking.

The `detail_is_fresh` helper is an optional **decision interface**, not a
replacement for the connector's source-end, detail identity, or fail-closed
checks. It requires a positive max age, a recorded fetch timestamp, a
compatible parser version, matching known listing fingerprints and no newer
trusted source update. It must not be used to suppress fresh catalogue
price/status/deadline updates.

## Coordination contract for agent 2

Agent 2 can independently use:
`detail_is_fresh(*, fetched_at, now, max_age, detail_version,
accepted_versions, previous_listing_fingerprint, listing_fingerprint,
source_updated_at=None) -> bool`.

A false result means **fetch or verify detail**, never "safe to skip lot".
A true result only allows reuse of *detail-only* evidence. Callers must
refresh and compare live catalogue state every run; 429, timeouts,
non-terminal coverage, mismatched identities and incomplete detail remain
fail-closed. Avoid changes to `ingestion.py`,
`mets_sync.py`, and `cdtrf_sync.py` in this PR.

## Validation / benchmark

- `PYTHONPATH=src pytest -q tests/test_detail_freshness.py`
- `PYTHONPATH=src python benchmarks/detail_freshness/benchmark.py`

The benchmark uses 1,585 **synthetic** records and three guaranteed business
changes. It emits reproducible wall-clock comparison timings but provides
**no claim about PostgreSQL throughput**. No CI/local execution result or
real SQL metrics is asserted until a runner executes these commands.

For empirical SQL baselines, run on a disposable SQLite/PostgreSQL test
database populated with copies of fully anonymized fixtures. Count
`before_cursor_execute` events and commits for (a) unchanged batches,
(b) price/status/date updates and (c) new identities. Capture count,
wall-clock, rows changed and diff of LotStatusHistory/LotPriceEvent. Never
run this benchmark against production.

## Pending before enablement

1. Inspect anonymized per-lot CDTRF before/after payload pairs from run
   `42393273402` to attribute the 1,584 updates by changed field.
2. Execute tests and an actual isolated DB benchmark; check numerical results
   and SQL count against the legacy path.
3. Regression check: archival, manual review, geo flags, duplicate IDs in a
   chunk, stale and incompatible detail versions, retries and concurrent run
   observation.
4. Align parser metadata and `detail_is_fresh` invocation with agent 2,
   then re-review before merging.

**No production reads or writes performed by this change.**
