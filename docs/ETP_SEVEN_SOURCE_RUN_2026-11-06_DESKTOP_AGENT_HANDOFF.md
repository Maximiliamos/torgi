# Desktop-agent handoff: seven-source production run, 6 November 2026

> **Operator-provided PowerShell/Home log; not yet independent Home DB verification.**
> Do not equate a green individual `LotSyncSourceRun` with complete source
> inventory, or this partial run with a successfully published GEO/map version.

## Where to inspect evidence

- **Home operator transcript** (local Home computer, not attached to GitHub):
  `D:\BankrotAI\etp-operator-logs\manual-standard-geo-map-20261106-233319.log`
- Earlier recovery/source-health run is present at the **start of the same
  operator transcript** and printed `RECOVERED_ALL_4`. The recovery
  script is [here](../scripts/diagnose-standard-source-circuits-no-vpn.ps1).
- **PostgreSQL durable run:** `LotSyncRun.id = 26ca1925-068f-4d86-ad7b-53d6482f5c21`,
  and seven `LotSyncSourceRun` rows of that `sync_run_id`.
  The operator log is the original aggregate evidence; inspect the DB
  read-only before making production decisions.
- **Backup and restore check**:
  `D:\BankrotAI\dr-backups\bankrotai-20261106-233324.dump`,
  approx. 693 MB, `restore_verification=passed`; no source row
  counts changed while the dump ran.
- **Code:** [PR #1128](https://github.com/Maximiliamos/torgi/pull/1128)
  was merged and deployed at `ce362342c8bb954cd2e55bb1efcbe8efe49c738a`.
  This SHA was the exact operator run preflight SHA.
- **Operator scripts, still in Draft [PR #1129](https://github.com/Maximiliamos/torgi/pull/1129)**:
  [full seven-source run](../scripts/run-standard-sources-geo-map-no-vpn.ps1),
  [guarded circuit diagnosis](../scripts/diagnose-standard-source-circuits-no-vpn.ps1),
  and [partial-run GEO/map continuation](../scripts/resume-partial-standard-geo-map-no-vpn.ps1).
  **Not merged to main and not auto-deployed**; obtain from the PR branch
  at pinned SHA. These tools are not proof of successful subsequent runs.
- Prior reference: [ETP stage checklist](ETP_PARSER_STAGE_CHECKLIST.md),
  [ROADMAP](ROADMAP.md), and
  [NISTP/CDTRF source handoff](ETP_NISTP_CDTRF_AGENT_HANDOFF_2026-11-06.md).

## Confirmed from the supplied operator transcript

The four prior source-health circuits were `half_open`, with
TLS/unknown-upstream errors. Bounded source-parser probes succeeded
for `torgi.gov.ru` (49 IDs), `lot-online.ru` (76),
`bidexpert.ru` (10), and `nistp.ru` (2 catalogue candidates,
one independently verified detail).
`RECOVERED_ALL_4` was printed and all four circuit states then
became `closed`. That operation updated *health state only*, not
auction lots.

The no-VPN seven-source preflight then found the expected seven specs,
TBankrot intentionally paused, Alfalot excluded, no active lease,
no source/global circuit, and NISTP detail cap 2/page. A fresh
PostgreSQL dump passed **restore verification**. The durable full run
then executed and ended `partial`:

| Source | Pages | Accepted/seen | Inserted | Updated | Archived | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `bidexpert.ru` | 1,469 | 6,321 | 417 | 5,904 | 0 | success |
| `torgi.gov.ru` | 1,895 | 17,179 | 1,342 | 100 | **35** | success |
| `lot-online.ru` | 46 | 3,089 | 128 | 61 | 0 | success |
| `nistp.ru` | 5 | 10 | 10 | 0 | 0 | success |
| `cdtrf.ru` | 5 | 5 | 2 | 3 | 0 | success |
| `m-ets.ru` | 5 | 4 | 0 | 0 | 0 | success |
| `torgi-russia.ru` | 1 | 23 source candidates | 0 | 0 | 0 | **failed** |

**Totals of persisted source-run counters: 1,899 INSERT, 6,068
UPDATE, 35 archive (all 35 from `torgi.gov.ru`).** Source
`items_seen` on the bounded ETPs reflects accepted eligible cards,
not the total catalogue inventory. These counters are not yet
independently reconciled against DB snapshots by this assistant.

**Precise failure:**
`Torgi Russia public region filter was not applied: source_region_id=1 expected=01 observed=[] titles=[]`.
The client received 23 page-one candidates but could not find
the required region proof. It correctly refused to persist them.
The cause **might** be drift in upstream `region_title`/nested region
data or a genuinely ignored provider filter. Do **not** relax
`PUBLIC_REGION_FILTER_EXPECTATIONS`, assign guessed regions, remove
the source guard, or count the 23 as accepted. Investigate with bounded
read-only HTML/first-party API evidence and exact ID/region checks.

**No GEO or map publication was started by this operator script**:
the loop explicitly required the whole nationwide run to equal
`success` and stopped at `partial`. Existing system background
jobs may operate independently, so check current GEO and MapDataset
read-only before concluding the public map was unchanged.

## Why NISTP, CDTRF, M-ETS only found 10/5/4

Not a low inventory at the provider. This was an **explicit five-page
discovery window**, with at most **two verified details per page**.
NISTP also requires provider-active status *and* independently
verified per-lot identity, initial price and normalized detail status
before persistence; failed/unknown details do not get imported.
CDTRF/M-ETS reject unproven price/property/status/lot details.
The past read-only selected-catalogue QA observed **2,716** NISTP,
**6,351** CDTRF, **3,083** M-ETS unique selected-scope IDs in
independent prior walks. These numbers are **not** current
production-accepted inventories and do not imply that all should be
imported. NISTP blockers [#1115](https://github.com/Maximiliamos/torgi/issues/1115)
and [#1116](https://github.com/Maximiliamos/torgi/issues/1116)
remain open.

## Analysis / next desktop-agent actions

**P0 — do not repeat the entire successful ingestion just to get GEO:**

1. Fetch the original Home log, inspect `LotSyncRun`,
   `LotSyncSourceRun`, latest map dataset and GEO status **read-only**.
   Ensure no later scheduled 09:00/21:00 run or other ongoing sync
   has changed the situation. Check that the 35 GIS archives are
   supported by its source-contract evidence. Do not infer from a
   backup that every later row was persisted.
2. Review the still-unmerged [partial-run
   GEO/map script](../scripts/resume-partial-standard-geo-map-no-vpn.ps1):
   it intends to **continue only GEO → MapDataset** for the exact
   `partial` run, requiring six successful source rows, zero writes
   by the failed `torgi-russia.ru`, explicit failed regional guard,
   no new run, no concurrent sync, correct SHA, and GEO not paused.
   Verify its actual exact Python/PowerShell API contracts, run targeted
   tests and a bounded safe dry-run of preflight before any promotion.
   It has **not** been run on Home.
3. With operator authorization and verified checks, run bounded pending
   GEO; report attempted / geocoded / failed / backlog. Publish
   immutable MapDataset using the existing builder; confirm `is_current`,
   `point_count`, public REG.RU S3 manifest/version and public map
   behavior. Do not mark all 7 sources successful when only six were.
4. Independently diagnose the Torgi Russia region failure from a
   bounded no-VPN first-party response with retained sanitized raw
   shape, exact source region ID, observed source lot identities and
   region labels. Preserve the **fail-closed** filter guard. Add
   regression tests before a parser fix and rerun only that source,
   not the six already successful ones.
5. **ETP coverage work is separate**: design verified full or
   restartable selected-catalogue walks for NISTP/CDTRF/M-ETS,
   with guarded detail batch processing, pagination/terminal proof,
   no invented lot metadata, and no missing-lot reconciliation
   until per-source source-wide acceptance. Avoid increasing
   five-page caps blindly.
6. Update [ETP checklist](ETP_PARSER_STAGE_CHECKLIST.md),
   [ROADMAP](ROADMAP.md), this handoff and PR descriptions with
   exact run IDs, evidence, merge SHA, deployment and status
   (implemented / tested / merged / deployed / operator-verified /
   source-accepted). Do not clear historical blockers without proof.

**Operational safety:** The user maintains a separate
`D:\8\Coding\TORGI_NEW` checkout: do not reset, clean, or
rewrite it. No bypassing HTTPS certificates, circuit breaker, region
guard, DB audit, backup or map publish gates. Do not put database
credentials or raw cookies in GitHub logs. **The operator's local
transcript is not automatically visible to the GitHub/Codex agent.**
Ask them to fetch it from Home or explicitly share it.

## Short task for the desktop agent

> Continue from `docs/ETP_SEVEN_SOURCE_RUN_2026-11-06_DESKTOP_AGENT_HANDOFF.md`
> on PR #1129. Read the Home log
> `D:\BankrotAI\etp-operator-logs\manual-standard-geo-map-20261106-233319.log`
> and DB run
> `26ca1925-068f-4d86-ad7b-53d6482f5c21`.
> Confirm the real `partial` with six sources success, 1,899 inserts,
> 6,068 updates, 35 GIS archives, and exact Torgi Russia regional
> filter refusal. Do not repeat six source ingestions. Review the
> guarded partial GEO/S3 continuation script and validate it
> before use; then, if approved, finish GEO/map with separate
> evidence. Diagnose the Torgi Russia filter drift separately.
> Preserve source-wide acceptance blockers and update ROADMAP,
> stage checklist and this handoff. No invented successes or
> bypassing integrity guards.
