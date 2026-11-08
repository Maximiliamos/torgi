# CDTRF semantic persistence and detail freshness audit

## Scope

Reviewed `src/bankrotai/services/batch_persistence.py`,
`src/bankrotai/services/ingestion.py` (read-only for this PR),
`src/bankrotai/connectors/registry/etp_incremental.py` (read-only),
`src/bankrotai/services/etp_cdtrf_canary.py`, `SourceLot`, `ProcessedLot`
and the source fingerprint path.

This PR does not edit `ingestion.py`, `mets_sync.py` or `cdtrf_sync.py`.

## CDTRF 1,584 / 1,585 update churn

GitHub Actions run `42393273402` recorded for CDTRF:

- 32 / 32 catalogue pages scanned;
- 1,585 items seen;
- 1 item inserted;
- 1,584 items reported updated;
- 0 items reported unchanged;
- 0 failed or archived.

The recurring false-change mechanism is identifiable in the current code:

1. `fetch_verified_cdtrf_lot` intentionally produces timezone-aware
   `Europe/Moscow` application dates through `_official_moscow_time`.
2. `batch_persistence._source_values` passes those dates through
   `_to_datetime`, which converts them to UTC and stores a timezone-naive
   value in `SourceLot`.
3. The existing `ingestion.py::_normalized_source_fingerprint` uses
   `normalized.application_deadline` directly when it is non-null instead of
   normalizing it through `_to_datetime`.
4. The next run therefore compares, for example, persisted
   `06:00` UTC-naive with fresh `09:00+03:00`. They represent the same
   instant but compare unequal as Python datetime values.

The aggregate shape (one new row plus every one of the 1,584 existing rows
reported changed) is consistent with this deterministic CDTRF date
representation mismatch. The historical run did not log per-field diffs, so
this is a code-level root cause, not a claim that a retained before/after
payload proves the offending field for every individual row.

A regression fixture in `tests/test_detail_freshness.py` asserts that UTC-naive
and Moscow-aware representations of the same deadline are semantically equal,
while a real date change remains significant.

## Business change policy

`compare_source_values` compares the persisted SourceLot projection rather
than transport representation. It includes identity, URLs, title/description,
classification, region/address/cadastre, price, status, procedure identifiers,
deposits, application and auction dates, public-offer schedule, next interval
price/date, document/inspection/contact information and active/archive state.

Money is normalized through `Decimal`. Datetimes are normalized to the same
UTC-naive instant before comparison. Nested raw values are stabilized
recursively.

Only an explicit transport/service allowlist may change without forcing a full
business upsert: listing/detail fetch fingerprints and timestamps, request
timing/ID, catalogue page, enrichment status/version and bounded-source scope
metadata. Any unknown raw field fails closed and is considered potentially
business-significant.

The batch guard also compares every ProcessedLot projection field except its
`last_update` bookkeeping timestamp. Therefore a SourceLot equality result
cannot suppress a ProcessedLot-only business update.

## Metadata-only refresh and audit preservation

A semantic skip does not mean "do nothing". One typed executemany UPDATE per
batch chunk advances:

- `last_seen_at`;
- `last_sync_run_id`;
- `missing_successful_runs = 0`;
- fresh transport raw metadata such as `listing_fingerprint`;
- `source_updated_at`, monotonically (an older source revision cannot replace
  a newer persisted revision).

Incoming raw metadata is merged over previously persisted raw metadata so a
catalogue-only refresh cannot erase successful detail-enrichment provenance.

A semantic skip does not create status or price-history events. Real price or
status changes continue through the existing upsert and existing
`LotPriceEvent` / `LotStatusHistory` paths. Canonical linkage,
`first_seen_at`, manual review protections and geo invalidation behavior are
unchanged.

`persist_changed_lots_batch` now returns `BatchPersistenceStats` with
`received`, `semantic_skipped`, `persisted` and `chunks` so an
orchestrator can distinguish a coarse pre-filter hit from an actual persisted
business change without changing the persistence API again.

## Batch-upsert use and SQL / transaction shape

`NationwideIngestionService._persist_page` uses
`persist_changed_lots_batch` by default. It performs an initial SourceLot
lookup, then calls batch persistence and commits once for that source page.

For an existing-row batch chunk the batch layer performs:

- SourceLot lookup;
- ProcessedLot lookup;
- metadata UPDATE only, when all rows are semantic skips;

or, for changed rows:

- SourceLot lookup;
- ProcessedLot lookup;
- ProcessedLot upsert;
- ProcessedLot ID reload;
- optional status-history insert;
- optional price-history insert;
- SourceLot upsert.

New identities also require CanonicalLot upsert + reload.

CDTRF run `42393273402` had 32 pages, so its source-page persistence path made
32 page commits. This excludes separate source-run/heartbeat bookkeeping
transactions outside `_persist_page`.

The reproducible synthetic benchmark uses 1,585 rows and batch size 500 on an
isolated in-memory SQLite database, counts SQLAlchemy
`before_cursor_execute` events and records one explicit commit per measured
batch call. Final measured values are recorded in the PR description after CI;
the test also asserts the expected statement counts so drift fails CI.

## Detail freshness interface / agent 2 coordination

PR #1142 owns M-ETS/CDTRF detail concurrency and does not overlap this PR's
files. Coordination was recorded in the PR #1142 conversation.

The reusable interface is:

`detail_is_fresh(fetched_at, now, max_age, detail_version,
accepted_versions, previous_listing_fingerprint, listing_fingerprint,
source_updated_at=None) -> bool`

`True` permits reuse only of detail-only evidence. It never permits skipping
fresh catalogue price/status/deadline comparison. Unknown/missing timestamps,
incompatible detail versions, changed/missing fingerprints, future timestamps,
expired detail, or a newer trusted source revision all return `False`.

PR #1142 currently does not persist both a detail fetch timestamp and a stable
per-listing revision. Therefore #1143 deliberately does not wire the helper
into #1142's connector files. The minimal future integration is for the detail
owner to persist those two pieces of evidence and call this helper before
scheduling a detail worker. HTTP/429/5xx, identity and source-end checks remain
fail-closed.

## Constraint-bound blocker

The visible `items_updated` counter is incremented in `ingestion.py` before
batch persistence is called, using the older source fingerprint. Because this
PR is explicitly forbidden from modifying `ingestion.py`, it can prevent the
unnecessary full database upsert but cannot make the existing run counter report
a semantic skip.

Minimal follow-up change in the ingestion owner is one of:

1. normalize non-null `normalized.application_deadline`,
   `application_start_at`/other comparable datetime values through the same
   UTC normalization used for persistence before fingerprint comparison; or
2. consume `BatchPersistenceStats.semantic_skipped` and move those rows from
   `items_updated` to `items_unchanged`.

Option 1 removes the CDTRF timezone false positive at its earliest decision
point. Option 2 also makes counters reflect the authoritative persistence
decision and is the more reusable interface.

## Verification commands

CI executes the repository-wide equivalents. Reproduction commands are:

```bash
PYTHONPATH=src pytest -q tests/test_detail_freshness.py tests/test_gis_batch_persistence.py
PYTHONPATH=src pytest -m "not postgres"
ruff check src tests
mypy src/bankrotai
python scripts/check_module_sizes.py
TEST_DATABASE_URL=postgresql+psycopg://... pytest -m postgres
PYTHONPATH=src python benchmarks/detail_freshness/benchmark.py
```

No production ingestion, GEO, S3 mutation or MapDataset promotion is performed
by this PR.
