# ETP stage checklist — operator contract

The bounded `etp_parser_checklist` module provides a **live read-only,
stage-by-stage diagnostic for all 22 registered target sources**, including
the existing Lot-online connector. It never imports, reconciles, geocodes,
archives or publishes data. Its purpose is explaining why an ETP adapter
stops, not certifying a complete source scan.

## NISTP + CDTRF: operator-verified milestone (2026-11-06)

Verified on 2026-11-06: integration [PR #1118](https://github.com/Maximiliamos/torgi/pull/1118)
at code head `831814fa5bbe65ec11b4280495db9165c22e7693` passed **10/10 CI checks** and
was merged to `main` as `837f22af93ca3890d6ea32f759bc5ec9a1e1a87a`:
[Production reliability](https://github.com/Maximiliamos/torgi/actions/runs/42135005571)
and [S3 diagnostics](https://github.com/Maximiliamos/torgi/actions/runs/42135005538).
Local focused suites passed **221 tests** (115 source/detail + 106 catalogue/structure).
CI ran full mypy successfully with pinned dependencies; local ambient GEO typing
errors do not override that CI result. Source-ID mutability and nullable diagnostic
values are corrected without relaxing lot identity or price guards.
Current `main` is `837f22af93ca3890d6ea32f759bc5ec9a1e1a87a` after that merge.
Post-merge deployment and source acceptance remain **NOT_TESTED**; source
blockers #1115/#1116 remain open. Passing PostgreSQL CI tests did not import source lots
into the production database. Passing S3 diagnostics did not publish a new map.

**Scope = source parser fixes and read-only diagnosis, NOT S0–S7 acceptance.**
The source-level work for this iteration is documented in
[ETP_NISTP_CDTRF_AGENT_HANDOFF_2026-11-06.md](ETP_NISTP_CDTRF_AGENT_HANDOFF_2026-11-06.md).
The stage diagnostic below runs on up to two pages; it does **not**
automatically ingest or replace the deeper no-VPN 10/30-lot evidence.

| Stage / evidence | NISTP (`nistp.ru`) | CDTRF (`cdtrf.ru`) |
| --- | --- | --- |
| Official catalogue/page contract | **PASS for two read-only 142-page walks:** 2,716 stable IDs and matching record digest | **PASS for two selected 64-page walks:** 6,351 stable IDs, identical digest |
| Source-selected active scope | **BLOCKED**: 43 terminal statuses (40 completed, 2 failed, 1 cancelled) leaked through selected active filters — [#1116](https://github.com/Maximiliamos/torgi/issues/1116) | **NOT_TESTED** for provider-wide completeness; the selected category/status partition was traversed twice |
| Individual detail + ID + price (fresh live sample) | **9/10 PASS, 1 BLOCKED**: `nistp.ru:502379:259`; no section for requested high lot | **10/10 PASS**, 2 HTML/Nuxt lot-title divergence review flags (identity/initial price checked) |
| Missing/exceptional per-lot details | **7/7 exact catalogue rows PASS, 0/7 individual sections:** 139,179,239,259,339,439,539; common trade-only URL, [#1115](https://github.com/Maximiliamos/torgi/issues/1115) | Older seven failed IDs are no longer on their original selected pages; **0/7 revalidated**, not a corrected-lot score |
| Non-destructive offline tests / integrated CI | **PASS**: 221 focused local tests across both sources and catalogue/structure regressions; 10/10 CI at `831814f` | Same integrated validation; fresh 10/10 source sample is separate live evidence |
| `complete_source_inventory` / `production_import` / GEO / map / `scheduler` | **NOT ACCEPTED / NOT TESTED here** | **NOT ACCEPTED by this new QA; staging/production E2E NOT TESTED here** |

All staged-source and detail-readiness observations in this snapshot
are **independent operator Windows logs**, not GitHub CI. Source PR stack
[#1105](https://github.com/Maximiliamos/torgi/pull/1105) through
[#1114](https://github.com/Maximiliamos/torgi/pull/1114) remained open,
unmerged on the last GitHub inspection; no automatic allowlist, production
write, GEO, archival or map activation may follow from these scores.
The zero-write claim applies to these *new diagnostic runs* only; older
CDTRF production records are described separately in the 02.11 snapshot.

Directly inspected local evidence (not GitHub artifacts):

- NISTP: `D:\BankrotAI\etp-evidence\nistp-diagnostic-20261106-002031.log`:
  two 142-page walks, 2,716 IDs and 43 terminal-status rows per pass.
  Examples: `501045:2` page 53, `500370:3` page 63, cancelled `499277:1` page 77.
  The cause of filtering leakage remains unproven; do not replace it with a hypothesis.
- CDTRF: `D:\BankrotAI\etp-evidence\cdtrf-full-readonly\cdtrf-pass-{1,2}-20261105T212709Z.json`:
  revision `426619f`, 64/64 pages, 6,351 IDs per pass, zero observed overlap/gap,
  matching digest `d2bc4f63001daaa29d3326b6544a8276577ce9e8e327990b72ffab093c937261`.
  These are later passes than the earlier 6,352-ID walk that observed live drift.
  Both per-pass reports retain `stable_point_in_time_snapshot=false`. The wrapper
  comparison's `true` value is an overstatement: equal ID sets do not prove an atomic
  provider snapshot or unchanged prices/statuses. Keep snapshot proof NOT_TESTED.
- Fresh details: `D:\BankrotAI\etp-evidence\two-source-detail-qa\nistp-ru-20261105T233118Z.json`
  and `cdtrf-ru-20261105T233118Z.json`: respectively 9/10 and 10/10;
  CDTRF has two title-review flags. These reports do not test the integrated SHA live.

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
- [x] Review and integrate the #1105–#1114 stack through #1118 after CI and
      source-contract review; final merge SHA is `837f22a`. Deployment remains separate.
- [x] Preserve the stack through #1117 in integration PR #1118; correct CI
      typing failures; verify 10/10 CI checks at `831814f`; merge as `837f22a`.
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

## All 22 ETPs: latest evidenced source-stage status (updated 2026-11-06)

Subsequent browser observations for M-ETS/Alfalot/Sberbank-AST are recorded in
[the 06 November browser report](ETP_METS_ALFALOT_SBER_BROWSER_2026-11-06.md).
M-ETS pages 1/2/153 are distinct and page 153 is browser-proven terminal for
the exact 3,056-item seven-category filter. Code commit `74eaa4a` implements a
two-pass full read-only proof and passes 29 focused tests, but its real no-VPN
execution remains `NOT_TESTED`; standalone land/agricultural categories remain
excluded. Alfalot's `stateFilter_GD` is confirmed as «прием заявки», while its
raw client paging request and property-only scope remain unproven. Sberbank
loaded unfiltered or privatization results, with the bankruptcy checkbox
unchecked; public client code caps displayed results at 10,000. These are
additional scope/pagination blockers, not new source acceptance.

Later no-VPN parser preflight at `9f75b567` observed live total 3,057 and
successfully parsed pages 1/2 (20 + 20, 40 unique) with next targets 2/3. The
one-item increase is live drift from the earlier browser snapshot, not an
error. Two agreeing upper/lower paginator blocks are now contract-checked.
The preflight intentionally stopped at page budget 2, so final page and two
full passes remain `NOT_TESTED`.

Full attempt `ddb1763b` advanced through page 152 with 3,040 unique IDs and no
duplicates, while the stable visible total stayed 3,059. Page 152 was full but
had no Next, so completeness is **FAIL** pending direct 152/153/154 tail proof;
pass 2 was not started. No archival or reconciliation is allowed.

For the other 20 sources the latest common live baseline is
[Home run 41950118761](https://github.com/Maximiliamos/torgi/actions/runs/41950118761),
artifact `etp-parser-stage-checklist`, inspected locally. The three later listed
workflow events were skipped and provide no new result. Old FAIL/BLOCKED entries
below mean **last observed result**, not a claim that the site is still broken now.
For all bounded samples, PASS applies only to observed rows. Counts are catalogue
candidates, not saved PostgreSQL lots. Source-specific newer NISTP/CDTRF evidence
is explicitly substituted in those two rows only.

| ETP | Catalogue and IDs | Pagination proof | Individual detail / price / status | Remaining blocker |
| --- | --- | --- | --- | --- |
| МЭТС `m-ets.ru` | PASS through live pages 1–152: 3,040 unique IDs, reported total 3,059 | FAIL at full page 152: no Next but 19 reported items unreached; pass 2 NOT_TESTED | Earlier browser 2/2 detail PASS / price PASS / scheduled status PASS; active-detail status NOT_TESTED | Direct 152/153/154 tail proof; no reconciliation/archival; selected scope omits land/agricultural categories |
| ЦДТ `cdtrf.ru` | PASS, 6,351 IDs per selected walk | 64 pages twice PASS, matching ID digest | Fresh 10/10 PASS for identity/initial price/status | Two title-review flags; >=30 fresh representative cards, full scope and downstream acceptance pending |
| НИСТП `nistp.ru` | PASS, 2,716 IDs per walk | 142 pages twice PASS, matching ID/record digests | Fresh 9/10; requested high section BLOCKED | 43 terminal statuses (#1116), seven catalogue-only high lots (#1115) |
| Альфалот `alfalot.ru` | PASS for 22 distinct IDs on active-filter pages 2/3; page 1 identity set not retained | Page 2 PASS; page 3 terminal PASS in browser; HTTP request contract NOT_TESTED | 2/2 property detail / price / `Идет прием заявок` status PASS | `stateFilter_GD` proves «прием заявки»; procedure types remain mixed and property-scope classification FAIL |
| Росэлторг `roseltorg.ru` | NOT_TESTED | NOT_TESTED | NOT_TESTED | Transport BLOCKED: ConnectionError |
| Сбербанк-АСТ `sberbank-ast.ru` | Bankruptcy catalogue NOT_TESTED; loaded cards are unfiltered/privatization | Filtered page 2 and last NOT_TESTED | Bankruptcy GoodsView detail / price / status NOT_TESTED | Bankruptcy checkbox remained unchecked; information landing is not a filtered catalogue |
| Фабрикант `fabrikant.ru` | PASS, 9 candidates | Page 2 and last NOT_TESTED | PASS / FAIL / FAIL | Missing individual prices and statuses |
| АРБбитЛот `arbbitlot.ru` | PASS, 40 candidates | Page 2 PASS; last NOT_TESTED | PASS / FAIL / PASS | Missing individual prices |
| Уральская ЭТП `etpu.ru` | PASS, 40 candidates | Page 2 PASS; last NOT_TESTED | PASS / FAIL / PASS | Missing individual prices; classification FAIL |
| Систематорг `sistematorg.com` | PASS, 16 candidates | Page 2 PASS; last NOT_TESTED | BLOCKED / FAIL / FAIL | Individual-lot isolation, prices and statuses |
| Центр реализации `centerr.ru` | Catalogue PASS; IDs FAIL, 21 candidates | Page 2 and last NOT_TESTED | NOT_TESTED | Duplicate/wrong IDs observed on page 2; #1093 code fix merged, live retest pending |
| Аукцион-центр `aukcioncenter.ru` | PASS, 30 candidates | Page 2 PASS; last NOT_TESTED | PASS / FAIL / FAIL | Prices, statuses and classification |
| Вердиктъ `vertrades.ru` | PASS, 40 candidates | Page 2 PASS; last NOT_TESTED | BLOCKED / FAIL / FAIL | Individual details, prices and statuses |
| RUSSIA OnLine `rus-on.ru` | PASS, 40 candidates | Page 2 PASS; last NOT_TESTED | PASS / FAIL / PASS | Missing individual prices |
| ЗаказРФ `zakazrf.ru` | PASS, 40 candidates | Page 2 PASS; last NOT_TESTED | PASS / FAIL / PASS | Missing individual prices; classification FAIL |
| Объединённая ЭТП `utpl.ru` | PASS, 40 candidates | Page 2 PASS; last NOT_TESTED | PASS / FAIL / PASS | Missing individual prices; classification FAIL |
| РТС-тендер `rts-tender.ru` | NOT_TESTED | NOT_TESTED | NOT_TESTED | Transport BLOCKED by explicit profile restriction |
| Профит `etp-profit.ru` | NOT_TESTED | NOT_TESTED | NOT_TESTED | Transport BLOCKED: TLS validation |
| Аукционы Сибири `ausib.ru` | NOT_TESTED | NOT_TESTED | NOT_TESTED | Transport BLOCKED: provider HTTP/access |
| uTender `utender.ru` | NOT_TESTED | NOT_TESTED | NOT_TESTED | Transport BLOCKED by explicit profile restriction |
| Электро-торги `electro-torgi.ru` | NOT_TESTED | NOT_TESTED | NOT_TESTED | Transport BLOCKED by explicit profile restriction |
| Lot-online `lot-online.ru` | PASS, 160 candidates | Page 2 PASS; last NOT_TESTED | FAIL / PASS / PASS | Diagnostic fetch_lot capability unsupported; working production importer is a separate path |

For all 22 rows, this evidence does not establish new source-scoped PostgreSQL
insert/update/deduplication, backup restore, GEO, MapDataset/S3/website publication,
or successful 09:00/21:00 MSK cycles: these acceptance stages remain NOT_TESTED.
Historical production data are not reset or reclassified by this checklist.
Torgi Russia is a separate source outside these 22 entries.

## Coverage of the 12 required acceptance stages

The current integrated machine schema still has 17 aggregate checks. Detailed
stage expansion exists in the separate open #1098 branch and is not included
in #1118. The following records that gap without claiming implementation:

| Required area | Current coverage / missing evidence |
| --- | --- |
| 1. DNS / HTTPS / HTTP / redirects | Aggregate transport exists; separate recorded DNS/TLS/redirect steps pending |
| 2. Correct catalogue / markup | Implemented; source-specific live contract required |
| 3. JavaScript / cookie / tutorial / filters | Cookie/tutorial check exists; separate JS and effective-filter evidence pending |
| 4. Procedure versus individual lot | Lot discovery exists; isolation is source-specific and incomplete for unaccepted sources |
| 5. Unique IDs / no duplicates | Implemented for observed pages; full DB uniqueness NOT_TESTED |
| 6. Every page / terminal / two passes | Source-specific full NISTP/CDTRF evidence above; general bounded probe cannot prove end |
| 7. Individual cards | Implemented for samples; full coverage NOT_TESTED |
| 8. Price / status / address / cadastre / area / dates | Aggregate price/status exists; dedicated field completeness checks pending integration |
| 9. Catalogue versus card | Source-specific QA exists for identity/price/status; all-field consistency remains incomplete |
| 10. PostgreSQL save / update / dedupe / restore | Acceptance protocol defined; source-specific staging/production proof NOT_TESTED |
| 11. GEO / dataset / S3 / actual website / links / filters | Aggregate GEO/map checks exist; detailed E2E proof NOT_TESTED |
| 12. 09:00 / 21:00 MSK repeat cycles | Aggregate scheduler exists; independent evidence for both slots and consecutive cycles NOT_TESTED |

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
