# DEZSTER release verification — 22 September 2026 (MSK)

**FINAL STATUS: NOT FULLY VERIFIED.** The candidate has not passed an independent full CI and production data verification. Do not deploy this worktree or run bulk data repair based on this report.

## Identity and scope

| Item | Evidence |
| --- | --- |
| Working branch | `codex/full-dezster-verification` |
| Committed candidate | `00893d5950feb4f37a2c577f347f756d8ce3c71c` |
| Working tree | Modified: `src/bankrotai/{cli,logic}.py`, `src/bankrotai/services/{ingestion,map_builder,read_model_repair}.py`, four related tests. New: `docs/source-status-contracts.md`, `scripts/audit_source_connectivity.py`, and this report. The local changes are not included in the committed SHA. |
| Current GitHub main and public WEB SHA | `cf9addaace4f6df5cdb42bc763689bd0d20e4bf0`, confirmed by GitHub API and `https://dezster.ru/deployment.json` on 22 September. |
| Deployed home API/worker image digest | Unverified from this session: Docker API denies access. Historical home deploy run `35262409072` succeeded on 18 September, but the follow-up script failed before completing all checks. |
| PR #506 | Merged 17 September; head `140d72155a34ac9dd983375719bc728713af0bd5`, merge commit `df7c356a600309c8d3fd6d94dbf50857cdb0a27a`. |
| Alembic | Local head and public `/health/ready` both report `09a1b2c3d4e5`. No migration differs between `origin/main` and candidate; **migration required: NO**. No migration was run in this audit. |

## Verification matrix

| Block | Status | Evidence | Defect / finding | Fix or next action | Remaining risk |
| --- | --- | --- | --- | --- | --- |
| Git / PR / CI | Partial | PR #506 merged; main `cf9adda` has successful CI jobs; candidate `00893d5` has one successful Production reliability workflow, but no independent full CI. | Working tree is dirty. | Review and commit local changes, then run full CI on that exact SHA. | High: tested local tree is not an immutable release. |
| Release readiness | Blocked | Local tests below; deployed WEB is main `cf9adda`. | Candidate has no full CI, clean build on its exact committed tree, or production verification. | Complete candidate CI and production plan before release. | High. |
| Backup / recovery | Verified for recorded backup | `C:\BankrotAI\backups\postgres\bankrotai-20260922-033002.json`: 100,450 ProcessedLot and 37,760 geo snapshots before/after and after restore; restore passed. | No post-candidate restore rehearsal. | Recheck restore close to any approved deploy. | Medium. |
| Database | Partial | Public ready returns 200, database `ok`, schema `09a1b2c3d4e5`; PostgreSQL CI passed on main. | Candidate PostgreSQL integration could not run here because Docker API access is denied. | Run integration tests in CI on exact candidate. | Medium. |
| Orphan reconciliation | Blocked | `C:\BankrotAI\logs\full-verification\20260922-001439\orphan-summary.txt`: 773 active TBankrot missing read model, 681 potentially recoverable links, 12 archived Lot Online orphans. | Identity matches alone do not authorize repair. | Run the read-only conflict report against production, review every conflict, then request separate approval before any bulk repair. | High correctness and map visibility risk. |
| Sources / freshness | Blocked | `source-freshness.csv`: newest `last_seen` is 7 September for TBankrot and 10 September for all other sources. Lot Online, Torgi Russia and BidExpert bounded pages succeeded. A 22 September retry of Torgi Gov with the Windows system trust store returned 62 items. TBankrot listing is access-limited. | Fresh ingestion is not evidenced. | Diagnose scheduler/source runs and perform only approved controlled ingestion after release evidence. | High stale-data risk. |
| TBankrot limitation | External limitation | Bounded search reports access limitation; partial sync must stay fail-closed. Exact public detail pages for control lots 7991236 and 7842289 returned HTTP 200 on 22 September and parsed successfully. | Nationwide bulk completeness unavailable. | Keep incomplete reconciliation disabled and expose limitation in diagnostics. | Medium. |
| Status lifecycle | Partial | Local regression tests pass for late active sibling promotion, manual geo override preservation and terminal handling. Source detail for lot 7842289 parsed `closed` on 22 September. | Source-specific terminal fixtures remain incomplete; downstream production lot 7842289 not verified. | Capture fixtures and verify lot 7842289 through DB, canonical, map, API and UI. | High. |
| Public offer / prices | Partial | Local backend tests pass. Live exact detail for lot 7991236 on 22 September parsed start 4,500,000 RUB, current/minimum 4,275,000 RUB. | Downstream production lot 7991236 not verified across DB, map, API and UI. | Verify all projections with authorized production read access. | High. |
| Photos | Blocked | `photo-coverage.csv`: active photo counts are 0/14,335 BidExpert, 0/8,559 TBankrot, 0/16,530 Torgi Gov; 2,011/2,012 Lot Online; 32,483/38,705 Torgi Russia. | Stored photo coverage is poor for three sources; presentation fallback not independently checked. | Check source contracts, persisted photos and UI samples. | Medium. |
| Dedup / canonical | Partial | Local late-active-sibling tests pass. | 773 active missing read models and production promotion state not resolved. | Read-only canonical conflict audit first. | High. |
| Geocoding | Blocked | 22 September report has 26,475 lots flagged `needs_geo_check`. Backups show geo snapshots rose from 36,330 on 17 September to 37,760 by 20 September and remained at 37,760 through the 22 September backup. | Current pause/resume state and absence of new 414 errors cannot be established from this session. | Inspect worker logs and progress through authorized production access. | Medium. |
| MapDataset | Partial | Health history shows 367 datasets on 17 September and two by 19 September; `health-20260922-102849.json` still shows two datasets, one current, healthy. Local dimensional guard tests pass. | Exact current/rollback versions, tile contents and cleanup execution log unavailable. | Verify rollback identity and tile contents before any further retention action. | High if cleanup is attempted. |
| API | Partial | Public and API live/ready return HTTP 200 on 22 September. Map and quality endpoints return 401 without login, as expected. | Authenticated responses and candidate API are unverified. | Run authenticated smoke against exact deployed SHA. | Medium. |
| Frontend | Local pass | 73 Vitest tests; TypeScript, ESLint and production build pass. Local Playwright: two passed, two production-only skipped. Initial build was blocked by Git ownership; rerun with process-local `safe.directory` passed. | Current production WEB is main, not candidate. | Run authenticated production browser E2E after approved deploy. | Medium. |
| Background jobs | Partial | Health log shows worker container running; prior no-VPN script failed while reading worker stderr after deploy. | Job completion, scheduler and Redis behavior not directly observed now. | Inspect worker/scheduler logs and progress. | Medium. |
| Security | Partial | Main CI security-related checks passed; public health routes available. | No candidate security gate or authenticated review. | Re-run exact candidate CI and review access controls. | Medium. |
| Performance | Partial | Main CI production reliability succeeded; local map tests pass. | Candidate map guard and read-model audit not load-tested on production-sized data. | Measure read-only audit and map build on staging snapshot. | Medium. |
| Recovery | Partial | Verified 21 September restore; current monitor reports two datasets. | Rollback image/dataset not tested for candidate. | Record immutable image digest and rehearse rollback. | Medium. |
| Production | Blocked | Public WEB SHA `cf9adda`, all four health endpoints 200; current home image digest unknown. | Candidate is not deployed or verified. | Obtain home image digest and authenticated smoke before release decision. | High. |
| No-VPN | Blocked | `finish-deploy` summaries: two validation failures, then a run with successful deploy workflow but failed follow-up verification. Old script expects `9cefb717…`, while current main is `cf9adda…`. | Old script is stale and must not be rerun unchanged. | Prepare a new reviewed no-VPN check for the exact candidate after approval. | High. |
| Observability | Partial | 22 September scheduled health logs are healthy; source and orphan evidence captured. | Data quality/freshness remains poor; no current authenticated quality report. | Add source freshness and orphan checks to release gate. | Medium. |
| Regression corpus | Partial | After the local fixes, backend 336 passed, 3 skipped; Ruff and Mypy pass. Frontend 73 passed; local Playwright two passed, two skipped. | PostgreSQL tests and production Playwright not rerun on candidate; control lots verified only at source. | Run exact-SHA full gates and production-only tests after deploy. | High. |

## Local changes made during this audit

The `audit-read-model-links` CLI command previously called `init_db()`, which runs `alembic upgrade head` and contradicts its read-only purpose. It now uses `read_session_scope()` without initialization. Its map visibility scan is limited to point-zoom tiles. A regression test verifies that the command does not call migration initialization or the write session.

Canonical promotion now carries an archived primary's manual geo override to an active projection even when that projection already has an automatic geo snapshot. A regression test checks the latest displayed coordinates after promotion.

## Production evidence and BEFORE / AFTER

| Metric | Recorded BEFORE / latest available | Verified AFTER for candidate |
| --- | --- | --- |
| ProcessedLot | 100,450 in backup metadata, 21 September | Unavailable |
| LotGeoSnapshot | 37,760 in backup metadata, 22 September | Unavailable |
| Active missing read model | 773, 22 September | Unavailable |
| Potential legacy links | 681, 22 September | Unavailable |
| Archived legacy orphan | 12, 22 September | Unavailable |
| Current MapDataset | Exactly one of two datasets, health monitor 22 September | Unavailable |
| Source freshness | Last seen 7–10 September, report 22 September | Unavailable |
| Points / tiles / photo and geo coverage | Partial evidence in `full-verification` folder; no comparable post-deploy capture | Unavailable |

## Rollback and restrictions

Before any approved deploy, record the existing API/worker image digest, WEB SHA, current and rollback MapDataset versions, database revision and fresh verified backup. Deploy only an immutable image matching a CI-verified candidate SHA. On failed health or data invariant, restore the prior application image and WEB release; keep the database and current MapDataset unchanged. Database restore or bulk repair requires a separate incident decision. Do not rerun the stale `finish-dezster-deploy-no-vpn` script, run migrations, apply retention cleanup, repair orphan links, change traffic/DNS, or alter any unspecified “second user” based on this report.

## Evidence locations

- `C:\BankrotAI\logs\finish-deploy\20260918-020058\summary.json` and `console.log`
- `C:\BankrotAI\logs\full-verification\20260922-001439\`
- `C:\BankrotAI\logs\health\health-20260922-102849.json`
- `C:\BankrotAI\logs\map-cleanup\dry-run-20260917-164033.json`
- `C:\BankrotAI\backups\postgres\bankrotai-20260922-033002.json`
- GitHub PR #506 and public GitHub checks for `cf9adda`; public `https://dezster.ru/deployment.json`
