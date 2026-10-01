# P7 — GEO Fast Drain

P7 migrates the historical pre-P6 GEO backlog out of multi-day retry timers and performs one controlled, CFO-first fresh pass through the modern P6 resolver.

## Goals

- Reclassify old GEO failures without losing evidence.
- Remove obsolete 4–7 day retry sleeps.
- Avoid releasing ~20k historical rows at once.
- Prioritize Central Federal District regions.
- Give repeated semantic misses one fresh resolver pass, then move them into deferred queues instead of restarting multi-day backoff.
- Keep network/provider failures outside the semantic retry budget.
- Publish truthful queue/ETA metrics.

## Historical migration

Only rows that do **not** already carry a P6/P7 classification are migrated.

- Operational/network history -> `network_wait`, semantic `attempt_count=0`, short retry.
- Repeated `no_match` -> `p7_queued`, old evidence preserved, at most two semantic attempts retained.
- Validation failures -> `p7_queued`, at most two semantic attempts retained.
- Unclassified/internal failures -> `p7_queued`, bounded internal budget retained.

Every migrated payload keeps:

- original attempt count;
- original next retry timestamp;
- P7 migration timestamp;
- existing provider attempt evidence.

## Controlled release

`p7_queued` is a held state. It is excluded from normal runnable/retry metrics until released.

The campaign releases bounded waves, ordered:

1. CFO regions first;
2. oldest failures first;
3. stable failure ID order.

Each wave is consumed by the normal serialized GEO batch engine, so P6 provider circuits, retry semantics, validation, cache and Redis lock remain active.

Default limits:

- 2,000 lots released per wave;
- 500 lots per resolver batch;
- 64 batches maximum;
- 3 hour application runtime ceiling.

## Difficult queue

A repeated historical semantic miss gets one fresh P7 pass.

If the new resolver still returns no coordinates, P6 moves it directly to:

- `deferred_no_match`, or
- `deferred_validation`.

It no longer sleeps for 4–7 days in the main queue.

Lots with no address and no cadastral input are reported separately as `deferred_bad_input`.

## Metrics

`geocoding_progress()` separates:

- `eligible_now`;
- `waiting_for_retry`;
- `network_wait`;
- `p7_held`;
- `deferred_no_match`;
- `deferred_validation`;
- `deferred_bad_input`;
- `terminal_failures`;
- `drain_remaining`;
- `drain_eta_seconds`;
- latest P7 campaign state.

The normal ETA remains scoped to work runnable **now**. The P7 drain ETA includes the held historical queue that the active campaign is expected to release.

## Production safety

The P7 workflow waits for the exact Home deploy SHA before touching production.

Host-disk gates:

- refuse to start below 10 GB free on C:;
- warn below 15 GB;
- while draining, monitor C: every ~20 seconds;
- below 8 GB, request a safe GEO pause between batches and fail the workflow.

The campaign marks the map dataset dirty once after successful GEO changes; the normal map publisher then creates the updated dataset.

## Operator controls

Admin-only API:

- `GET /api/operations/geocoding/fast-drain/plan`
- `POST /api/operations/geocoding/fast-drain`

The normal Operations panel exposes held/deferred counts and the active P7 campaign progress.
