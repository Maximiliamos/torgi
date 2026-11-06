# ETP stage checklist — operator contract

The bounded `etp_parser_checklist` module provides a **live read-only,
stage-by-stage diagnostic for all 22 registered target sources**, including
the existing Lot-online connector. It never imports, reconciles, geocodes,
archives or publishes data. Its purpose is explaining why an ETP adapter
stops, not certifying a complete source scan.

## NISTP + CDTRF: operator-verified milestone (2026-11-06)

Integration follow-up: 115 focused tests, Ruff and mypy for the three corrected
modules pass on the integration branch. Source-ID mutability and nullable
diagnostic values are corrected without relaxing lot identity or price guards.
Merge, CI for the integrated SHA, deployment and production acceptance remain
pending. Source blockers #1115/#1116 remain open.

**Scope = source parser fixes and read-only diagnosis, NOT S0–S7 acceptance.**
The source-level work for this iteration is documented in
[ETP_NISTP_CDTRF_AGENT_HANDOFF_2026-11-06.md](ETP_NISTP_CDTRF_AGENT_HANDOFF_2026-11-06.md).
The stage diagnostic below runs on up to two pages; it does **not**
automatically ingest or replace the deeper no-VPN 10/30-lot evidence.

| Stage / evidence | NISTP (`nistp.ru`) | CDTRF (`cdtrf.ru`) |
| --- | --- | --- |
| Official catalogue/page contract | **PASS for two read-only 142-page walks:** 2,716 stable IDs and matching record digest | **PASS for two selected 64-page walks:** 6,351 stable IDs, identical digest |
| Source-selected active scope | **BLOCKED**: 43 terminal statuses (40 completed, 2 failed, 1 cancelled) leaked through selected active filters — [#1116](https://github.com/Maximiliamos/torgi/issues/1116) | **PARTIAL**: selected categories/statuses only, not full source inventory; catalogue drift observed |
| Individual detail + ID + price (fresh live sample) | **9/10 PASS, 1 BLOCKED**: `nistp.ru:502379:259`; no section for requested high lot | **10/10 PASS**, 2 HTML/Nuxt lot-title divergence review flags (identity/initial price checked) |
| Missing/exceptional per-lot details | **7/7 exact catalogue rows PASS, 0/7 individual sections:** 139,179,239,259,339,439,539; common trade-only URL, [#1115](https://github.com/Maximiliamos/torgi/issues/1115) | Older seven failed IDs are no longer on their original selected pages; **0/7 revalidated**, not a corrected-lot score |
| Non-destructive offline tests | **83/83** broader focused tests on prior SHA; **9/9** NISTP link-shape diagnostic | Same broader focused suite **83/83**, independent fresh 10/10 read-only evidence |
| `complete_source_inventory` / `production_import` / GEO / map / `scheduler` | **NOT ACCEPTED / NOT TESTED here** | **NOT ACCEPTED by this new QA; staging/production E2E NOT TESTED here** |

All staged-source and detail-readiness observations in this snapshot
are **independent operator Windows logs**, not GitHub CI. Source PR stack
[#1105](https://github.com/Maximiliamos/torgi/pull/1105) through
[#1114](https://github.com/Maximiliamos/torgi/pull/1114) remained open,
unmerged on the last GitHub inspection; no automatic allowlist, production
write, GEO, archival or map activation may follow from these scores.
The zero-write claim applies to these *new diagnostic runs* only; older
CDTRF production records are described separately in the 02.11 snapshot.

**Operator checklist to move source status forward (do not auto-check):**

- [x] NISTP two full selected-catalogue traversals with matching IDs/hashes
      (coverage observed, **active-scope completeness NOT certified**).
- [x] CDTRF two full **selected-catalogue** traversals with matching IDs/hashes
      (not complete provider inventory).
- [x] NISTP parser recognizes own numbered lot fields without borrowing
      fields from malformed sibling sections; four earlier errors recovered.
- [x] CDTRF HTML/NUXT verified title discrepancy handled without weakening
      `tradeId`, `tradeLotId`, source code and initial price guards.
- [x] Original failed IDs are pinned; changed catalogue pages cannot
      silently substitute another candidate; per-page progress shown.
- [x] NISTP high-numbered seven catalogue rows found independently via
      official exact-trade/lot search; same seven link census has no
      lot-specific href; *catalogue-only* limitation documented.
- [x] Fresh no-VPN NISTP 9/10 and CDTRF 10/10 sample observed; two CDTRF
      title-review cases explicitly recorded.
- [ ] Review and merge stacked #1105–#1114 **after** CI and source-contract
      review; record final merge SHA and deployment separately.
- [ ] Resolve NISTP [#1116](https://github.com/Maximiliamos/torgi/issues/1116)
      status-filter leakage; two **clean** active-scope passes.
- [ ] Resolve or explicitly quarantine [#1115](https://github.com/Maximiliamos/torgi/issues/1115)
      missing high-number detail sections; do not claim per-lot price
      verification from catalogue rows.
- [ ] CDTRF review two diverging titles; representative >=30 current
      details, complete source-scoped acceptance plan, stable page evidence.
- [ ] Independent staging canary and restore-verified backup; P11 exact SHA,
      PostgreSQL/GEO/MapDataset/WEB reconciliation, scheduled runs.
- [ ] **Only then** amend source allowlists / production accepted states
      on documented owner approval. Never change these flags based on QA alone.
- [ ] Resume the next ETP source **CenterR**, then **Sistematorg**
      price completeness and separately **Торги России** incident;
      keep independent P1 workstreams and source-specific blockers.

## Run on trusted Home ingestion worker

```powershell
docker exec bankrotai-home-ingestion-worker python -m bankrotai.services.etp_parser_checklist --all --pages 2 --detail-probes 1
docker exec bankrotai-home-ingestion-worker python -m bankrotai.services.etp_parser_checklist --source nistp.ru --pages 2
```

Scheduled Home read-only workflow: `ETP parser stage checklist (Home read-only)`.
Download its `etp-parser-stage-checklist` artifact for the exact job SHA
and read `etp-parser-stage-checklist.json`. Its other artifact file
preserves the source-by-source progress log. A successful workflow means
diagnostics ran and an artifact was retained; it **does not** mean 22
parsers were accepted or imported.

## Stages

`catalogue_url` → `transport` → `catalogue_markup` →
`lot_discovery` → `stable_lot_ids` → `pagination_next` →
`last_page_proven` → `individual_detail` → `detail_identity` →
`initial_or_current_price` → `auction_status` →
`property_classification` → `cookie_or_tutorial_modal` →
`production_import` → `geocoding` → `map_publication` →
`scheduler`.

A stage reports `PASS`, `FAIL`, `BLOCKED`, `NOT_TESTED`, or
`N/A`. `first_failure` identifies the earliest observed exception
category and page, where available. Provider URL query strings, HTML,
raw exception text, account identities and individual lot data are
not logged. Browser cookie/tutorial popups do not apply to HTTP-only
connectors; real browser adapters must assess them independently.

The single-page or two-page sample cannot establish:
- true terminal catalogue page or completeness without independent evidence;
- verified auction identity/price/status correctness for *all* listings;
- correctness of production database imports and map publication;
- freshness or correctness of automatic 09:00 / 21:00 Moscow schedules.

These remain `NOT_TESTED` until the full source-specific acceptance
workflow and downstream database/GEO/map validation pass. Never change
production allowlists based on this diagnostic alone.

## Torgi Russia

The full nationwide source deliberately requires a proven terminal page,
with `reconcile_missing=False` to avoid historical destructive archival.
The one-page FAST source retry is **not a full scan**: it must set
`require_source_end=False`, retain `max_batches=1`, and never archive.
A passing read-only source probe plus queued FAST task is not evidence
that the follow-on Celery sync completed. Inspect its durable source-run
result before declaring recovery.

## Safeguards

- Capture unique lot IDs and count in aggregates only.
- Do not defeat CAPTCHA, change TLS verification or access hidden APIs.
- Do not use a successful page-1 response as a production readiness claim.
- Never automatically import an unaccepted source.
- Separate retryable access failures from HTML schema failures.
