# Post-run forensic audit — nationwide 8 Nov 2026, 21:00 MSK

**Status: PARTIALLY VERIFIED. Not a 7/7 acceptance or a deployed incremental release.**

## Durable evidence available in the conversation

The operator queried Home PostgreSQL **read-only**, returned the exact
`LotSyncRun` and seven `LotSyncSourceRun` rows and the current
`MapDataset` row:

- Run `f572bdff-3db1-41bf-94a4-1f75ab690232`:
  `partial`, heartbeat/finished `2026-11-08 21:19:12.457923` UTC.
  Triggered by 8 Nov 21:00 MSK schedule.
- Totals: **87 inserted, 6,235 updated, 1,383 archived**. These
  counters are source-run aggregates, not an item-by-item forensic log.
- The current MapDataset in PostgreSQL was
  `20261108T215500614188Z-r6-bundle-s3`,
  `point_count=73488`, published `2026-11-08 21:56:31.816738` UTC,
  AFTER run completion. This alone does **NOT** verify that every
  one of 87 inserted lots received GEO or a public REG.RU S3 marker.

| Source | Pages | Seen | New | Updated | Archived | Status / complete |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GIS `torgi.gov.ru` | 1,873 | 16,945 | 12 | 6 | **49** | success / yes |
| Lot-online | 45 | 3,042 | 4 | 34 | **2** | success / yes |
| Torgi Russia | 1,366 | 20,427 | 50 | 234 | 0 | **partial** / no |
| BidExpert | 1,620 | 7,200 | 0 | 4,380 | **1,332** | success / yes |
| M-ETS | 153 | 1 | 1 | 0 | 0 | success / **no** |
| CDTRF | 32 | 1,595 | 17 | 1,578 | 0 | success / **no** |
| NISTP | 143 | 202 | 3 | 3 | 0 | success / **no** |

Torgi Russia explanation:
`source completed with 1 quarantined malformed record(s); archival forbidden`.
That is a **fail-closed partial**, not a 7-source success.

**Critical temporal comparison**: the earlier 7 Nov run
`7810df7b-fb90-4c9f-9039-546ed151172b` recorded **1,191**
M-ETS eligible lots across 155 pages. The 8 Nov run recorded **only
one** across 153 pages. This is a major acceptance collapse
requiring candidate/filter/detail-rejection evidence, not a normal
catalogue count. NISTP similarly decreased from 815 to 202 seen.
The prior 7 Nov run was overall failed and is not a clean baseline,
but the differences are meaningful diagnostic signals.

## What can and cannot be proven

- BidExpert's **1,332** archive counter is confirmed, but the exact
  cause breakdown is NOT independently known. Do not assume
  `missing_after_two_complete_syncs` or restore/undo without
  proving every candidate. GIS 49 and Lot-online 2 likewise require
  reason-coded SQL evidence and previous complete run anchors.
- BidExpert's current production spec is `category="all"`.
  PR #1160 proposed `realty`, while PR #1161 retained `all`.
  Switching would narrow the existing source inventory (including
  land) and may change missing-lot archive semantics. No switch
  until scoped before/after comparison of IDs and archive rules.
- CDTRF has **1,578** `items_updated`. This counter does NOT prove
  1,578 material DB changes or harmless no-op writes. Prove through
  SQL-statement instrumentation and immutable before/after evidence.
  An incremental fast-path should skip semantically unchanged rows,
  not merely rename updates.
- M-ETS `success` with `complete_source_run=false` and
  1 eligible lot is not production acceptance. Need separate
  catalogue candidates, accepted categories, individual detail
  attempts, parsed, rejected-by-reason, and final source-end proof.
  These values may be absent from historic checkpoint; **do not invent**.
- NISTP must retain precise provider-active states, strict
  individual-lot identity, current price/status verification,
  quarantine of shared-trade high-numbered lots (#1115) and
  terminal status leakage (#1116). `complete_source_run=false`
  remains unaccepted scope.
- Map's 73,488 points are a dataset-level metric. Need independent
  public S3 manifest/version and sampled real WEB tile/marker proof.
  Count canonical new lot GEO with/without coordinates; a simple
  time window is only a best-effort reconstruction.

## Changes in integration PR #1162

- Consolidates one reviewable release from overlapping #1160/#1161;
  #1162 branches from #1161, **does not merge** #1160 blindly.
- Restores original eight-category M-ETS provider scope including
  category 11, matching the existing full-catalogue acceptance probe.
- Restores CDTRF acceptance of independently verified land; leaves
  other identity/status/price checks intact.
- Retains BidExpert's current `all` until reason-coded archive audit.
- Denies cache reuse if there is no **explicit** independent
  `detail_verified_at`. Existing `last_seen_at` is a catalogue
  observation, NOT a detail verification. Writer is also fail-closed
  against unproven reused rows.
- Offline CI regression and contract fixes are being verified against
  the new PR SHA, including correct timeout/retry budgets and
  scheduled `incremental` mode.
- Added `scripts/audit_20261108_nationwide_readonly.py` to
  export DB run, per-source checkpoint counters, archive reasons
  within the run time window (NOT exact source attribution), previous
  two full runs per source, current new-lot GEO states, NISTP detail
  evidence and optional read-only public REG.RU S3 manifest validation.
  This is source code in a PR: **not yet executed on Home**.

## Evidence collection / production release gate

1. Obtain a live no-VPN Home shell, check exact deployed image SHA and
   active `LotSyncRun`/lease. The last visible Home deploys for
   latest main `2b2f3a27...` failed closed during an active
   8 Nov sync. Therefore HEAD CI ≠ deployed code.
2. Run the new audit extractor **read-only** on Home, from a review
   checkout or Python stdin in the existing container. Suggested
   output `D:\BankrotAI\etp-operator-logs\audit-20261108.json`.
   It must NOT trigger full ingestion, GEO or map publication.
3. Confirm archive group totals and follow several sampled
   BidExpert/GIS/Lot-online IDs back to source listing/detail and
   previous two accepted complete runs. Treat timestamp-window
   aggregations as approximate unless event logs give precise
   run-attribution.
4. Diagnose M-ETS and NISTP collapsed acceptance funnels and
   CDTRF high update count by bounded source evidence, independently
   of code changes. Do NOT remove fail-closed identity/price guards.
5. Confirm GEO with/without counts of new source lots, current
   MapDataset, public S3 manifest and WEB marker sample. Record any
   backlog explicitly.
6. Verify **all** required CI checks are green on the final
   integration SHA. Do not disable regressions to get green.
7. Only after fresh backup/restore proof and no active lease,
   merge one integrated PR; rely on fail-closed Home deployment,
   verify exact Home/API/WEB/Cloudflare SHAs and public health;
   no forced worker stop or unnecessary second full nationwide run.
8. Update Roadmap, ETP checklist and this handoff with distinct
   statuses **CODE / TEST / MERGED / DEPLOYED / HOME VERIFIED /
   SOURCE ACCEPTED**; never mark a stage complete by inference.

Current status at handoff: branch changes and CI underway;
**merged=NO, deployed=NO, Home post-run SQL audit=NOT EXECUTED,
new S3 verification=NOT EXECUTED, complete seven-source acceptance=NO**.
