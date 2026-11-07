# Performance baseline — seven sources and full pipeline

Baseline evidence is frozen to commit `e434b630c06b1ed3675f7dec571f44a4be9db67c` and GitHub Actions run `42393273402` (attempt 2). The durable ingestion run id is `ba82db42-f93f-42ea-99bf-3374fb4e1d3e`.

## Safety and evidence boundary

Run 42393273402 is a **partial/failed run**, not a successful end-to-end performance result. No live ingestion, GEO drain, S3 mutation or MapDataset promotion is performed by this benchmark work. The post-reconciliation MapDataset/REG.RU S3 verification step was skipped after `torgi-russia.ru` failed, including its targeted retry.

The canonical redacted copy is `benchmarks/fixtures/run-42393273402.json`. Unknown measurements stay `null`; they are never converted to zero or inferred from rows/second.

| source | status | pages | seen | inserted | updated | unchanged | archived |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| torgi.gov.ru | success | 1892 | 17158 | 25 | 175 | 16958 | 27 |
| lot-online.ru | success | 45 | 3036 | 12 | 3 | 3021 | 0 |
| torgi-russia.ru | failed | 455 | 6780 | 84 | 149 | 6547 | 0 |
| bidexpert.ru | success | 1627 | 7054 | 0 | 3634 | 3420 | 0 |
| m-ets.ru | success, non-reconciling | 153 | 1190 | 7 | 15 | 1168 | 0 |
| cdtrf.ru | success, non-reconciling | 32 | 1585 | 1 | 1584 | 0 | 0 |
| nistp.ru | success, non-reconciling | 142 | 794 | 0 | 0 | 794 | 0 |
| **total including partial source** | **6/7 success** | **4346** | **37597** | **129** | **5560** | **31908** | **27** |

Confirmed changed rows = inserted + updated = **5689**. The CDTRF anomaly is confirmed: 1585 seen, 1 inserted, 1584 updated, 0 unchanged.

The torgi-russia failure was region 36/page 2: 24 records, 23 normalized, with `empty_title: 1` and `unknown_parser_drop: 23`.

## Implemented benchmark contract

`benchmarks/read_only_pipeline.py` now implements rather than merely documents the audit:

- per-source p50/p95 HTTP attempt latency;
- detail attempts, cache hits, retries and DB transaction counters;
- per-source queue-wait aggregation;
- source duration only when both source start/finish timestamps exist;
- single explicit `geo_span`, `map_build_span` and `pipeline_span`;
- fail-closed validation of negative/ambiguous metrics, duplicate sources and duplicate pipeline spans;
- critical path is suppressed unless all seven source statuses are successful;
- tracked file sizes and top 25 files;
- Python direct-import cycle candidates via AST;
- dependency inventory and SHA-256 of Python/Web lock files;
- workflow trigger inventory, home-runner labels, concurrency groups and cross-group contention candidates;
- pre/post git-status comparison proving the repository audit itself is read-only.

A source metric remains `null` unless its corresponding telemetry event exists. An unrelated HTTP event no longer makes cache hits/retries/DB transactions appear as zero.

Telemetry JSONL source events use one approved `source` and one of: `http`, `detail_attempt`, `cache_hit`, `retry`, `db_transaction`, `queue_wait`. Pipeline events are source-less (or `source="__pipeline__"`) and use `geo_span`, `map_build_span`, `pipeline_span`. Durations are monotonic milliseconds; counters are non-negative integers.

## Reproduce

No production credentials are required:

```bash
python benchmarks/read_only_pipeline.py \
  --snapshot benchmarks/fixtures/run-42393273402.json \
  --repo . \
  --out /tmp/torgi-performance-baseline.json

pytest tests/test_performance_baseline_benchmark.py
pytest -m "not postgres"
ruff check src tests benchmarks
mypy src/bankrotai benchmarks/read_only_pipeline.py
python scripts/check_module_sizes.py
```

With a disposable PostgreSQL 16 test service only:

```bash
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/bankrotai_test pytest -m postgres
```

The CI workflow runs the targeted benchmark test before the full SQLite suite, includes `benchmarks/` in ruff and the benchmark module in mypy, runs the repository audit against the complete checkout, retains the module-size guard and runs the existing PostgreSQL integration suite.

## Repository audit baseline

At exact baseline SHA e434b630, the recursive Git tree was complete (`truncated=false`): 577 tracked blobs totaling 5,315,468 bytes. Large code modules included `src/bankrotai/gui.py` 313012 bytes, `src/bankrotai/scrapers.py` 147092, `src/bankrotai/api.py` 106305, `src/bankrotai/geo.py` 91561, `src/bankrotai/services/geo_backfill.py` 71463, `src/bankrotai/tasks.py` 53328 and `src/bankrotai/services/ingestion.py` 51836. Size is a review signal, not proof of runtime cost.

There were 50 workflow YAML files at the baseline SHA. `.github/workflows/phase3-lite-full-reconcile.yml` had a dedicated concurrency group, but that group cannot serialize unrelated home-runner workflow groups. The implemented audit now enumerates the actual home-runner workflows and concurrency groups on the checked-out SHA so queue contention can be reviewed without guessing.

## Coordination with adjacent PRs

This PR deliberately does not edit runtime files owned by the neighboring work:

- PR #1139 — preflight/checkpoint recovery;
- PR #1140 — GEO/Map publication fence;
- PR #1141 — mixed-source ingestion concurrency;
- PR #1142 — M-ETS/CDTRF detail concurrency;
- PR #1143 — detail freshness/batch persistence;
- PR #1145 — ETP hardening;
- PR #1146 — map retention.

Those PRs expose the exact runtime locations where telemetry eventually belongs. This PR defines and validates the neutral JSONL aggregation interface, avoiding competing edits to their source files.

## Pull-request S3 safety guard

While validating this PR, the existing `REG.RU S3 diagnostics` workflow was found to expose production S3 credentials to same-repository `pull_request` runs and to execute authenticated operations including bucket CORS updates and a temporary probe object. That behavior is unrelated to the benchmark and violates a read-only PR expectation.

This PR therefore makes one isolated workflow safety change: every `pull_request` run is forced to `HAS_S3_CREDENTIALS=false` even if repository secrets are technically available. Authenticated diagnostics remain available only outside PR context (currently explicit `workflow_dispatch`). A regression test asserts that the pull-request guard is evaluated before the credential check. The benchmark/pytest jobs themselves perform no production ingestion, GEO drain, S3 write, or MapDataset promotion.

## Remaining interface blocker

The baseline run did not emit request attempt timing, cache-hit counters, transaction counters, queue timestamps or verified GEO/Map stage spans. Therefore those baseline values cannot be reconstructed safely.

Minimal follow-up after adjacent PRs stabilize: add non-mutating telemetry emission at their existing boundaries to produce the event types above, tagged by run id, exact SHA, source, stage/host and outcome. Do not put lot ids, URLs with secrets or raw payloads in metric labels. No runtime instrumentation is added here because doing so would require editing the same ingestion/detail/persistence/GEO/Map files currently owned by PRs #1140-#1143.
