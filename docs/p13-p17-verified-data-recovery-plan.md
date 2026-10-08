# P13–P17: восстановление достоверной публичной карты
Дата исходной проверки: 2026-10-08. **Plan / NOT ACCEPTED / NO PRODUCTION MUTATIONS.**

## Verified starting point
- Home run: https://github.com/Maximiliamos/torgi/actions/runs/37767683234
- Artifact: `p13-live-public-map-impact` (ID 11546965721), exact main SHA `fd8b1ee4061f083e2e4fd85cd669b7563049aafb`.
- Current immutable MapDataset 45,380 points; 46,581 non-archived primary rows with mapped coordinates.
- Sequential filter: status 21,849; real-estate/no VIN 21,708; non-rental sale 21,708; strict cadastral GEO 7,086; fresh active independent canonical proof 2,741.
- Stage-specific additional exclusions: status 24,732; category/VIN 141; rental/title 0 **after preceding gates**; strict GEO 14,622; independent fresh source 4,345.
- 1,550 of 2,741 eligible candidates have unknown region; final MapBuilder spatial validation can lower eligibility further.
- Existing coverage guard: min 0.5 of 45,380 (22,690); even 21,849 rows surviving only status checks are below this. Thus the legacy *polluted map* is not a defensible completeness oracle.

## Non-negotiable invariants
1. No unverified rental, movable/vehicle, closed, duplicate, untrusted cadastral centroid, or unproven stale source on the *new* public map.
2. No `auction_status=active` inferred from missing status or merely existing row; do not lower `MIN_MAP_COVERAGE_RATIO` as a workaround.
3. Paused optional `torgi-russia.ru` and TBankrot do not establish fresh automatic coverage without actual valid current source proof.
4. Every production write after verified three-generation backup, checksum, isolated PostgreSQL restore, canary and rollback plan. No hard delete; no massive archive.
5. Do not merge #932 or promote replacement map while quality and rollback gates remain unresolved.

## Execution sequence

### R0: read-only reasons and provenance (first)
Add to `p13-live-public-map-impact` a reason matrix under the *same SQL predicates*:
- `auction_status` distribution for all 46,581 and for each canonical provider: truly closed vs unknown vs stale active projection.
- 21,708 pre-GEO candidate breakdown by `current_geo_source`, `needs_geo_check`, missing/uncertain cadastral match, null `region_code`, duplicate centroids and locality mismatch. Display overlap counts explicitly.
- 7,086 pre-source candidates split between absent canonical link, inactive/closed canonical status, paused/not actually synchronized provider, proof older than 72 h, and no auction-type proof.
- Proving SourceLot must be attributed by *canonical SourceLot.source_system*, never by `ProcessedLot.source_system`; historical TBankrot primary rows may be supported by another active source.
- Do not put PII/addresses into CI artifacts. Add deterministic SQLite+PostgreSQL regression fixtures.
**Accept:** aggregate report whose stage totals reconcile exactly and distinguish genuine closure from ingestion/projection defects.

### R1: repair the source lifecycle, not statuses by guess
For each live automatic provider, collect effective paused/circuit state, last successful complete sync, fresh SourceLot volume, source_status distribution, and canonical sibling association coverage. Investigate Torgi Russia without enabling it merely to satisfy the test. Repair any broken SourceLot→CanonicalLot→ProcessedLot linkage, missed full-sync watermark and status mapping. Unknown remains ineligible; truly closed stays closed.
**Accept:** reproducible cross-source provenance and timestamp evidence; affected active rows return only after independent proof.

### R2: recover cadastral GEO in bounded canaries
Segment the 14,622 strict-GEO rejects by provider and reason. Prioritize Yaroslavl, then highest-impact regions. For cadastral records require exact normalized cadastral number from the provider payload plus geometry/centroid derived from the *matched object*, not a geocoder address centroid or first feature. Mark uncertain points `needs_geo_check`; never fabricate coordinates. Run small staged batches with before/after diff, local mismatch and rollback, scaling only after verified restore.
**Accept:** zero unverified cadastral address fallback; measured geographic impact and successful revalidation checks.

### R3: disentangle freshness from false negatives
Check whether hard-coded 72 h proof is appropriate for each provider's *successful reconciliation cadence*, rather than stretching 72 h globally or pretending stale source data are current. If needed, implement source-specific freshness based on trustworthy sync evidence and preserve fail-closed semantics. Keep TBankrot isolated.
**Accept:** source-specific expiry tests, independent proof and accurate freshness on real sample rows.

### R4: shadow map and legitimate re-baselining
Build candidate dataset privately; keep old public immutable MapDataset and S3 pointers intact. Report precision and geographic/source/category coverage against (a) old count **for regression visibility**, and (b) a *reviewed, eligible current baseline* for the new strict contract. The old 50%-of-polluted-map rule cannot be used as a target requiring false `active` classifications. Changing baseline requires explicit reviewed migration with measured legitimate closures, not silently weakening or bypassing a guard. Record blocked domains and recovery steps.
**Accept:** no critical quality defect; real verified active coverage, per-region/source regressions reviewed; a rollback-ready immutable dataset.

### R5: production canary → controlled promotion
Verify three backup generations and isolated restore, exact Home/REG.RU/Cloudflare SHA, P1/P11, WEB smoke, S3 manifest, and both workflows `push`/`workflow_dispatch` plus rerun. Validate five owner examples through SourceLot→CanonicalLot→ProcessedLot→GEO→MapDataset→S3→browser; VIN absence from a single field is NOT proof of moped removal. Run seeded 50 land / 30 commercial premises / 20 house-or-flat manual/independent spot checks, record actual denominators.
**Accept:** no incorrectly public owner cases, zero critical pollution counts, no unverified coordinates, published exact-SHA dataset and rollback demonstrated.

### R6: close administrative debt
After accepted release update README, ROADMAP, runbooks, historic archive and remaining naming/secret migration separately; do not rename workflows or production secrets during data recovery. Version must be bumped rather than repointing existing `v0.3.5` release at a new SHA.

## Stop and rollback rules
STOP if a data write lacks verified restore, any newly published lot fails eligibility, a paused provider is treated as active, a provider freshness check is speculative, source/region coverage unexpectedly collapses, an exact-SHA step is skipped, or the control five / 100 sample fail. Preserve existing immutable dataset and document the original evidence. Do not declare 100% until **code + database + source freshness + exact GEO + public map + rollout evidence** all pass.
