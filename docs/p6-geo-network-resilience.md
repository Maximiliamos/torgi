# P6 — GEO Network Resilience

P6 separates transport/provider failures from real geocoding misses so transient VPN, DNS, TLS or upstream outages cannot put lots into multi-day semantic retry backoff.

## Behavior

- External provider health is tracked in Redis with per-provider circuit breakers.
- A global external-network circuit opens when multiple independent external GEO probes fail in the same window.
- The probe task checks TLS reachability for NSPD, IK12 and torgi.gov.ru and checks local Photon separately.
- Network/TLS/DNS/5xx/429 failures use short operational retries and **do not increment the lot semantic attempt counter**.
- Real `no_coordinates` results get two bounded retries (30 min, then 2 h) and are then moved to `deferred_no_match`.
- Validation mismatches get two bounded retries (5 min, then 30 min) and are then moved to `deferred_validation`.
- Deferred lots are automatically eligible again when their GEO input changes.
- IK12 recovery may still recover `deferred_no_match` cadastral misses without consuming the normal retry budget.
- Local Photon remains usable while the external-network circuit is open.
- Provider circuits close automatically after successful health probes.

## GEO states

- `queued` — semantic retry is scheduled/runnable.
- `network_wait` — temporary operational dependency problem; semantic retry budget preserved.
- `deferred_no_match` — reasonable current strategies returned no coordinates.
- `deferred_validation` — candidates were found but rejected by validation.
- `terminal` — internal/unclassified retry budget exhausted.
- `resolved` — coordinates were accepted.

## Operational diagnostics

`geocoding_progress()` now exposes:

- `eligible_now`
- `waiting_for_retry`
- `network_wait`
- `deferred_no_match`
- `deferred_validation`
- `classified_percent`
- provider/network health snapshot
- current external circuit state

Production health treats external GEO network degradation as a warning rather than a global application failure. The home deploy diagnostic prints the same network/deferred counters.

## Safety

P6 intentionally does **not** bulk-reset existing historical retry timestamps. Reclassifying and draining the old backlog belongs to P7 so it can be done as a controlled production campaign with before/after metrics.
