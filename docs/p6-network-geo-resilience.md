# P6 — Network & GEO Resilience

P6 separates infrastructure/provider outages from lot-data quality failures.

## Goals

- A VPN/DNS/TLS/provider outage must not consume a lot's semantic GEO retry budget.
- A real provider response with no usable coordinates must not sleep for 4–7 days and then repeat the same query.
- Network/provider health must be observable and automatically recoverable.
- Local Photon must remain usable when the external network is degraded.

## GEO failure states

| Status | Meaning | Retry policy |
| --- | --- | --- |
| `waiting_network` | Correlated external-network degradation is confirmed | 1m → 3m → 5m → 15m, semantic attempt counter unchanged |
| `waiting_provider` | One provider/local service is degraded | 1m → 5m → 15m → 60m, semantic attempt counter unchanged |
| `deferred_no_match` | Providers responded but no validated coordinates exist | no timer retry; reconsider only after input/strategy/provider changes |
| `deferred_validation` | Candidate coordinates failed locality/region/confidence validation | no timer retry |
| `deferred_bad_input` | No usable GEO input | no timer retry |
| `queued` | Runnable GEO work | normal worker queue |
| `resolved` | Valid coordinates were persisted | complete |
| `terminal` | Legacy/manual terminal state | complete until strategy changes |

P7 is responsible for reclassifying the pre-P6 historical backlog into these states and performing the fast drain.

## Provider health

The runtime keeps health state for:

- local Photon;
- NSPD;
- IK12;
- Nominatim.

Two consecutive operational failures open a provider circuit. Open circuits are short-lived and are mirrored in Redis so all Celery processes see the same state.

Provider error details are never exposed through health diagnostics. Only category, latency and a short one-way error fingerprint are retained for correlation.

## Global network health

Every minute the maintenance queue probes:

1. local Photon HTTP;
2. DNS + TCP/TLS to NSPD;
3. DNS + TCP/TLS to IK12;
4. DNS + TCP/TLS to torgi.gov.ru.

Two independent external probe failures mark the external network `down`. One failure is `degraded`.

A recovery from `down` requires two consecutive healthy probe cycles. When recovery is confirmed, `waiting_network` rows are released immediately and GEO is re-queued.

A provider-specific recovery releases matching `waiting_provider` rows.

## VPN/network profile correlation

The Windows production health script records a SHA-256 fingerprint of the active default-route, adapter and DNS configuration. It stores only the fingerprint and counts, not the gateway/DNS addresses themselves.

A fingerprint change next to a correlated external-network failure is evidence of a routing environment change (for example VPN on/off), but the system never assumes that a fingerprint change itself is a failure.

The container-side probe also records a runtime routing/DNS fingerprint for cross-process diagnostics.

## Safety

- PostgreSQL/Redis/Photon failures remain distinguishable from external-network failures.
- A single provider timeout does not automatically become a global VPN/network outage.
- TLS-only probes do not close circuits opened by HTTP 429/5xx/read-timeout; those require a real successful provider request or circuit expiry.
- Network/provider failures do not increment `GeoFailure.attempt_count`.
- Deferred rows are excluded from runnable GEO selection, avoiding repeated identical requests.
- P6 does not bulk-reset existing historical retry timestamps; that migration belongs to P7.
