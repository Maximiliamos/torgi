# Seven-source incremental fast path

This release keeps full catalogue coverage while removing avoidable detail and persistence work from the twice-daily nationwide ingestion.

## Scheduled 09:00 / 21:00 mode

The existing Celery beat key remains `refresh-nationwide-sources-full` for operational compatibility, but its mode is now `incremental`.

`incremental` means:

1. all configured source catalogues are still scanned to source end;
2. independent sources run with the existing bounded source concurrency;
3. M-ETS and CDTRF materialize the complete catalogue before detail validation;
4. a fresh unchanged verified M-ETS/CDTRF row reuses persisted detail evidence;
5. new, changed, stale or ambiguous rows still fetch detail and fail closed;
6. persistence still performs semantic comparison before writing updates.

Manual `full` remains available for forced all-detail verification.

## Detail reuse contract

Reuse is deliberately conservative. A cached detail is reusable only when:

- the source row is not archived;
- persisted property identity evidence includes cadastral number and lot URL;
- detail evidence is no older than seven days;
- catalogue status still matches persisted source status;
- catalogue price still matches a persisted source price;
- M-ETS title still matches;
- when a prior catalogue fingerprint exists, the new fingerprint matches it.

Any missing or contradictory evidence forces a real detail request.

The first run after deployment may therefore be much heavier than later runs, because legacy rows without sufficient fresh evidence are refreshed once.

## Catalogue-first processing

M-ETS and CDTRF now complete catalogue discovery before expensive detail work. The validated catalogue pages are held only in process memory. Detail validation then consumes that bounded snapshot, avoiding a moving page/detail/page pipeline.

The design remains non-reconciling for these selected-scope sources: absence from M-ETS/CDTRF never authorizes archival.

## Resume

M-ETS and CDTRF checkpoints persist their source cursor and crawler state. A targeted incremental retry first discovers a fresh catalogue and compares the entire processed prefix plus the previous-page digest with the checkpoint.

- exact prefix proof: resume at the next page;
- any shift, missing page or digest mismatch: reject the checkpoint and restart.

NISTP pagination is stateful form/postback. It does not currently have a proven portable cursor, so retries restart safely instead of pretending to resume. Sources with reconciliation semantics likewise do not use an unproven partial cursor.

## Fail-fast preflight

Before scheduled `full` or `incremental` work creates a heavy run, a bounded read-only preflight runs in parallel.

- all sources: public reachability/circuit probe;
- M-ETS: first selected catalogue page plus verified property detail;
- CDTRF: official API candidates plus up to five verified details;
- NISTP: catalogue row plus up to two independent detail proofs.

A failed preflight returns `source_preflight_failed`; no heavy nationwide run is created.

## Timeouts and retries

CDTRF catalogue and detail requests use bounded 5s connect / 20s read windows. M-ETS detail requests use the same bound. Transient retry backoff is shortened and 401/403/429 remain hard fail-closed outcomes.

Per-host request-rate limits remain conservative; concurrency does not bypass the host-global limiter.

## Metrics

Each source checkpoint/result records pages/rows per second, catalogue discovery milliseconds, detail attempts, unchanged-detail skips, detail-cache hit rate, retry count, ETA and resumable cursor/checkpoint where supported.

## Existing fast paths retained

This release preserves bounded source concurrency, two-worker M-ETS/CDTRF detail execution, semantic batch persistence, and the separate GEO queue/map publication fencing.

## M-ETS scope

Scheduled M-ETS remains exactly seven business categories: `36,34,39,37,38,28,41`, statuses `0/1`.

Standalone land category `11` is excluded. A mixed lot containing a target building/premise and associated land is not rejected merely for mentioning land.
