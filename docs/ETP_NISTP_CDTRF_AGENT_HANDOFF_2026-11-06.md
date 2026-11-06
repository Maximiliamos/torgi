# ETP agent handoff: NISTP + CDTRF — 6 November 2026

> **Canonical operational handoff for the next agent.** Read this with
> [ETP parser stage checklist](ETP_PARSER_STAGE_CHECKLIST.md),
> [ROADMAP](ROADMAP.md), and
> [22-source acceptance protocol](ETP_22_ACCEPTANCE_AND_LOT_VERIFICATION.md).
> This is a snapshot from operator-supplied no-VPN PowerShell logs and
> GitHub code/PRs, **not evidence of production deployment**.

> **Subsequent independent M-ETS follow-up (2026-11-06):** code commit
> `74eaa4a` on `codex/mets-full-readonly-20261106` adds a zero-write full
> two-pass runner for the browser-confirmed seven-category filter. Its 29
> focused tests pass, but a live no-VPN execution is `NOT_TESTED`; category
> IDs 11/40, individual active-card validation, DB/GEO/map and scheduling are
> still open. This does not alter the NISTP/CDTRF acceptance state below.

> **M-ETS live preflight update:** SHA `9f75b567` parsed no-VPN pages 1/2
> (20 + 20 items, 40 unique, total 3,057, next 2/3). Raw HTML contains two
> agreeing paginator blocks. PR #1124 enforces agreement across both. This is
> not final-page/two-pass acceptance and made no DB/GEO/map changes. Home
> deployment of prior merged SHA `0029c004` succeeded in run 42207409497.

> **M-ETS full-attempt update:** `ddb1763b` reached page 152 with 3,040
> unique IDs and no duplicates, but the stable reported total was 3,059 and
> the full page 152 had no Next. Completeness remains FAIL; pass 2 was not
> started. Probe pages 152/153/154 before revising the terminal contract.

> Tail probe `3b951b88` proved pages 152/153/154 are distinct and full at
> newer total 3,082; page 154 omitted Next prematurely. #1124 continues only
> when a full page plus reported total proves another page, and still requires
> exact total/incomplete terminal. Direct pages 155/156 remain pending.

## 1. State: implementation advanced; production acceptance NOT complete

Checklist refresh 2026-11-06: #1118 at `831814f` passed all 10 CI checks
(Production reliability run 42135005571, S3 diagnostics 42135005538).
221 focused local tests passed. #1118 merged to `main` as `837f22a`;
post-merge deployment/source acceptance have not been established. The checklist
now includes the last evidenced result for all 22 sources and explicit gaps
against the 12 required areas. The full detailed machine-stage expansion in
open #1098 has not been integrated into this stack.
The CDTRF comparison wrapper marks stable snapshot true, but both raw per-pass
reports keep it false; retain only the proven equal 6,351-ID sets with no
observed duplicates/gaps, not provider point-in-time snapshot acceptance.

Integration review on 2026-11-06: branch `codex/etp-stack-integration-20261106`
preserves the complete stack through #1117 and already contains current main.
115 focused NISTP/CDTRF tests and Ruff passed. Fixed the #1105 CI type error
(`source_id` writable base attribute overridden by read-only property), plus
nullable numeric/regex guards in later diagnostics. Focused mypy passes;
full local mypy additionally reports six existing GEO request-parameter typing
errors, which must be checked against CI dependencies. No deployment or source
acceptance follows from these local checks.

| Track | Verified as done | Remains open |
| --- | --- | --- |
| NISTP active-filtered catalogue | Two independent 142-page walks, 2,716 unique IDs each, identical ID and record digests, known terminal page | **43 terminal-state rows** leaked through active filter; `effective_active_scope_proven=false` (issue [#1116](https://github.com/Maximiliamos/torgi/issues/1116)). Two clean active-scope scans not proven |
| NISTP individual detail | Original 30-card sample: 20/30; after targeted parser fixes, 4/10 previously failed cards recovered; fresh sample **9/10** | Procedure `502379` high lot numbers 139/179/239/259/339/439/539: exact catalogue rows verified **7/7**, but no independent detail section or lot-specific row link; [#1115](https://github.com/Maximiliamos/torgi/issues/1115) |
| CDTRF selected catalogue | Two 64-page walks, **6,351 distinct IDs** each with matching digest, no observed gaps/duplicates in checked selected partition | `complete_source_inventory=false` and `stable_point_in_time_snapshot=false`; live catalogue drifts. These selected-catalogue scans are not all-source acceptance |
| CDTRF individual detail | Original 30-card sample 23/30; HTML vs Nuxt discrepancy isolated, code fixed without relaxing URL/trade/lot ID and initial-price checks. **Fresh 10/10 detail sample PASS**; 2 title disagreements flagged for review | Prior exact 7 IDs shifted off former catalogue pages so targeted original-ID recheck **0/7**; do not claim these seven recovered or assume absent site-wide |
| QA and diagnostics | Operator proved 80/80, then 83/83 regression tests on preceding commits; latest NISTP link-shape diagnostic 9/9 tests, 7/7 catalogue rows. Read-only reports saved locally | New doc-only handoff and complete stacked PR CI/merge/deploy still need independent verification; no signed production release/acceptance was established |

**Interpretation:** the parser coding and evidence-first diagnosis phase for
these **two sources is done for now**. The owner can prioritize other source
development; do not falsely mark NISTP/CDTRF `production_accepted`. Historical
production CDTRF records existed before these QA runs (see
[2026-11-02 rollout snapshot](ETP_ROLLOUT_STATUS_2026-11-02.md));
`production_database_writes=0` applies only to **these new QA probes**.

## 2. PR stack: preserve order and status

All were **open and unmerged at the last GitHub read (2026-11-06)**.
The parent of each subsequent PR is the preceding feature branch.
Never rebase/merge directly without examining the delta against `main` and
running the required CI; stale historical README/rollout counts are not proof.

1. [#1105](https://github.com/Maximiliamos/torgi/pull/1105): NISTP two full read-only catalogue passes (base `main`, not Draft at last check).
2. [#1108](https://github.com/Maximiliamos/torgi/pull/1108): explicit out-of-scope terminal-status diagnostics.
3. [#1109](https://github.com/Maximiliamos/torgi/pull/1109): independent 30-per-source NISTP/CDTRF detail QA, no-VPN PS runner.
4. [#1110](https://github.com/Maximiliamos/torgi/pull/1110): safe named failure reasons and original ID evidence.
5. [#1111](https://github.com/Maximiliamos/torgi/pull/1111): structural evidence for missing NISTP sections and CDTRF trade/title differences.
6. [#1112](https://github.com/Maximiliamos/torgi/pull/1112): target-lot NISTP section isolation, CDTRF verified Nuxt lot name with review flag, ID-pinned rechecks.
7. [#1113](https://github.com/Maximiliamos/torgi/pull/1113): always-on per-page outcome/elapsed progress; explicit fresh versus old-ID recheck.
8. [#1114](https://github.com/Maximiliamos/torgi/pull/1114): strictly read-only NISTP official high-lot exact search and safe first-party link-shape inspection.

All source changes are restricted to **reviewed Draft/stacked or base PR
branches**, not certified `main`; no broad production enablement is authorized.

## 3. Exact operator evidence and chronology

| Step | Result | Evidence file / code |
| --- | --- | --- |
| Full active-filtered NISTP walk | Twice 142 pages, 2,716 IDs; 40 `Торги завершены`, 2 `Торги не состоялись`, 1 `Торги отменены` found in requested active scope | #1105 / #1108 and NISTP full-scan reports |
| Full selected CDTRF walk | Twice 64 pages, 6,351 selected-partition IDs, stable digest | CDTRF full catalogue QA |
| Initial independent 30+30 individual cards | NISTP **20/30**, CDTRF **23/30**; 17 failures | `nistp-ru-20261105T221514Z.json`, `cdtrf-ru-20261105T221514Z.json` |
| Exact failed-page reasons | 10 NISTP: 6 missing requested sections on trade 502379 and 4 incomplete sections; 7 CDTRF: title-or-identity mismatch | `nistp-ru-20261105T222934Z.json`, `cdtrf-ru-20261105T222934Z.json` |
| Structural probe | NISTP trade 502379: 100 numbered tables, highest number 128; other 3 single-table cards had different row structure, and `494080:3` valid target fields but six defective siblings. CDTRF 2/7 original trade+lot IDs and prices match, HTML H1 differs from Nuxt lot name; 5 IDs already moved from old catalogue pages | `failed-card-structure-20261105T230040Z.json` |
| New code exact-ID recheck | **80 tests passed**; NISTP 4/10 recovered, six missing detail sections; CDTRF 0/7 because all seven historical IDs no longer occupy old pages | `nistp-ru-20261105T231643Z.json`, `cdtrf-ru-20261105T231643Z.json` |
| Fresh current cards | **83 tests passed**; NISTP 9/10, only `nistp.ru:502379:259` missing detail; CDTRF **10/10**, two H1/Nuxt title-review flags | `nistp-ru-20261105T233118Z.json`, `cdtrf-ru-20261105T233118Z.json` |
| High-number NISTP official exact form | 7/7 exact catalogue rows, positive listing prices and active status, but no independent per-lot detail | `nistp-high-lot-source-form-20261105T234119Z.json` |
| First-party link census | **9 tests passed**; 7/7 rows have only `/bankrot/trade_view.php` with `trade_nid`, **0/7** lot-scoped links | `nistp-high-lot-source-form-20261105T235353Z.json`, #1114 |

Local reports are on the operator's Windows machine at
`D:\BankrotAI\etp-evidence\two-source-detail-qa\`. **They are not GitHub
artifacts or container files; do not claim the agent has read them unless the
operator uploads them.** This handoff records validated aggregate console
output, not a newly fetched independent card capture.

## 4. Source-specific hard guards

**NISTP:** Keep the original public form method `GET`, checked repeated
`trade_state[]` filters, strict page/terminal proof, unique external IDs,
own numbered-lot table heading/anchor/field-price-status validation, sibling
duplicate ID rejection, and fail-closed missing requested lot sections.
For 502379 high numbers (139/179/239/259/339/439/539), retain
`catalogue_only=true` conceptually and
`individual_lot_detail_verified=false`; never synthesize price, cadastre,
address, legal rights or status from neighbouring tables.
**Missing individual-detail evidence is different from missing catalogue
identity.** Do not re-request the same known trade-level link in a loop.
Provider might expose an alternative route in the future; prove it from its
own public HTML/navigation before using it. Track [#1115](https://github.com/Maximiliamos/torgi/issues/1115).

**NISTP filter issue:** the two stable catalogue walks still showed 43
terminal statuses despite selected active checkboxes. Explicit status
classification can prevent closed lots from being shown as active, but
silently skipping 43 rows does **not prove source completeness**.
Keep scope gate false and track [#1116](https://github.com/Maximiliamos/torgi/issues/1116).

**CDTRF:** Check `tradeId`, `tradeLotId`, official trade code and HTML
versus Nuxt initial price; retain `source_title_disagreement_review_required`
when HTML H1 differs from verified Nuxt `lot.name`.
A fresh `10/10` sampled PASS with two flags is not full source acceptance.
The seven original IDs drifting to other catalogue positions may not be
replaced with arbitrary first candidates and called recovered. Keep the
selected four statuses and property-category criteria; reject 401/403/429,
overlarge, invalid, repeated or incomplete pages.

For **both**: `source_accepted=false`, `complete_source_inventory=false`
until independently proven, `reconcile_missing=false`,
`archival_allowed=false`; no new QA production PostgreSQL writes,
GEO, MapDataset, map publication, automatic Celery sync, or
`ETP_ACCEPTED_SOURCE_IDS` expansion. No disabling TLS or bypassing access
restrictions.

## 5. Agent's next actions (strict sequence)

### P0 — integrate without changing production (now)

- [ ] Inspect current `main`, PRs #1105–#1114, #1115/#1116 and CI. Verify
      branch ancestry, tests and conflicts. The stack is **not merged**.
      Avoid creating a fresh independent replacement that loses tests/docs.
- [ ] Work from a clean dedicated worktree/branch. Preserve the operator's
      unrelated worktree `D:\8\Coding\TORGI_NEW`; do not reset/clean/rewrite it.
- [ ] Run static checks + focused tests for NISTP parser, CDTRF details, 30-lot
      QA, structural diagnostics, source-form probe, per-page progress,
      identity/pinned-lot refusal and production write isolation.
- [ ] Compare with existing source connectors and source-status contracts.
      Keep status/price/identity evidence separate and consistent;
      ensure no new enabled scheduled ingestion from these PRs.
- [ ] Integrate by normal reviewed PR ordering or an explicitly reviewed
      replacement integration PR after proving ancestry, no silent squash of
      missed tests. Update this handoff, checklist and roadmap with final
      SHA/CI/merged/deployed separately.
- [ ] Do **not** trigger production import/reconcile/archive/geo/map as a
      side effect of merging diagnostic code.

### P1 — close the two outstanding NISTP quality gates (separate)

- [ ] [#1116](https://github.com/Maximiliamos/torgi/issues/1116):
      bounded no-VPN evidence for terminal statuses in active-filtered
      selection; determine filter semantics, exact per-lot statuses and why
      selection leaks. Do not run two full 142-page scans unnecessarily.
      Write tests for `Торги завершены`, failed, cancelled, active,
      unexpected and status drift. Rerun complete filtered walks only
      after root cause and scope strategy are verified.
- [ ] [#1115](https://github.com/Maximiliamos/torgi/issues/1115):
      keep seven trade 502379 high lots catalogue-only; do not repeatedly
      scrape known trade URL. Investigate *only* a newly observed official
      lot-specific document or provider-supported route, with independent
      per-lot identity/price/status guards.
- [ ] NISTP production gate stays **blocked** while either is open.

### P1 — finish CDTRF validation, then hand off to next source

- [ ] Review exact two HTML/Nuxt title mismatches and preserve provenance
      without changing hard `tradeId`/`tradeLotId`/price checks.
- [ ] Reconcile chosen selected-category/status partition vs full source and
      account for live list drift. Re-run independent source snapshots
      and >=30 representative verified individual details **when source
      acceptance is actually being pursued**, not to inflate an earlier
      fresh 10/10 smoke result.
- [ ] Only with operator authorization: isolated staging DB canary with
      verified restore backup, exact SHA, collision/idempotency tests;
      then PostgreSQL/GEO/MapDataset and P11+scheduled-run gates.
- [ ] Until then CDTRF remains **sample_verified**, not newly
      `production_accepted` by this work.

### P1 — next source after this two-source milestone

- [ ] Resume **CenterR `centerr.ru`**: confirm real result-row IDs across
      pages (exclude promotional sidebar anchors), terminal page, each
      numbered individual lot detail/price/status and two stable scans.
      Keep DB/map writes disabled until evidence.
- [ ] Then **Sistematorg `sistematorg.com`**: fix severe price
      completeness regression in earlier production snapshot (18/3,361),
      prove per-lot shared-trade isolation and existing DB migration/
      collision safety without blanket overwrite or missing-lot archive.
- [ ] Continue separate high-priority **Торги России** source recovery
      only against its own current incident/evidence and exact-SHA gates;
      do not mix this track into NISTP/CDTRF acceptance.
- [ ] For remaining ETPs, follow the 22-source
      [S0–S7 acceptance protocol](ETP_22_ACCEPTANCE_AND_LOT_VERIFICATION.md)
      and the [stage checklist](ETP_PARSER_STAGE_CHECKLIST.md); produce
      source-specific reports rather than declaring all 22 finished.

## 6. Agent work discipline: what to write after each session

1. In PR description, list **implemented / tested locally / observed on
   source / merged / deployed / production accepted** as separate columns
   or statements; never equate them.
2. Update [ROADMAP](ROADMAP.md) **and**
   [ETP_PARSER_STAGE_CHECKLIST](ETP_PARSER_STAGE_CHECKLIST.md)
   **and this handoff** after every material source-contract change.
3. Link each claim to a real PR/issue, exact SHA, test count and
   an independently observed log or report; mark absent evidence
   as `NOT_TESTED`. Historical snapshots are not live dashboards.
4. Keep a single authoritative blocker list (#1115, #1116 plus CDTRF
   acceptance), and never silently clear unresolved gates.
5. Do not say 'НИСТП и ЦДТ полностью готовы' on the basis of
   fresh 9/10 and 10/10 samples. What is complete is
   **this iteration of parser correction and no-VPN diagnosis**.

## 7. Short prompt for the next coding agent

> Continue `Maximiliamos/torgi` from the read-only NISTP/CDTRF
> handoff (docs/ETP_NISTP_CDTRF_AGENT_HANDOFF_2026-11-06.md).
> First inspect PRs #1105–#1114 and issues #1115/#1116, check exact
> Git SHAs, code review and CI, and preserve source/lot identity,
> price and fail-closed status gates. The completed milestone is
> NISTP 9/10 fresh detail sample plus 7/7 **catalogue-only** high
> lots; CDTRF 10/10 fresh detail sample with two title review flags,
> not full production acceptance. Do not repeat known trade-level
> requests or write to production DB, reconcile, archive, GEO or map.
> Update docs/ROADMAP.md, docs/ETP_PARSER_STAGE_CHECKLIST.md and the
> handoff on every material step. Prioritize NISTP #1116 and #1115
> without blocking independent CenterR next-source work; handle
> Sistematorg and Torги России separately. Do not claim source
> acceptance without complete source and staging+production evidence.
