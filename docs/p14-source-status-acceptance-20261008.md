# P14 — статус и доказательства архивирования (08.10.2026)

**Состояние: диагностика подтверждена, массовое исправление данных НЕ разрешено.**

## Production evidence

Read-only Home run: https://github.com/Maximiliamos/torgi/actions/runs/37790138076
Main SHA: `f35c68b200b078a64ca0a8fe71429392618eb94e`.

- 46,793 current unarchived mapped primaries; `active=21,446`, `scheduled=615`, `expired=24,715`, `unknown=17`.
- **All 24,715** expired primaries have a *direct* SourceLot with `source_status=active` but `is_active=false`, `is_archived=true`, `archive_reason=missing_after_two_complete_syncs`.
- Archive breakdown by primary source: torgi-russia.ru **12,329**; torgi.gov.ru **6,472**; bidexpert.ru **5,131**; lot-online.ru **783**.
- **0** expired primaries have an active non-archived *direct* SourceLot; **1,731** have an active fresh *canonical sibling* SourceLot.

These findings mean the primaries disappeared from accepted complete source reconciliations; the string "active" alone is NOT valid proof that the auctions continue. A sibling may be a different source and/or processed projection. Unknown or paused-provider source evidence is not proof either.

## Rules and follow-up

1. **No blanket status flip or bulk unarchive.** Require a newly observed active status from the original provider, or independent, correctly linked, current canonical evidence with exact identity.
2. Source metadata **must** prove complete pagination. An identical non-empty page at another cursor is a broken source, not a valid final page: fail the sync and skip missing-lot reconciliation.
3. Investigate the new P14 read-only fields `recent_source_sync_run_evidence` and `largest_archive_date_cohorts`: identify accepted complete runs and archive-date spikes for each provider; use those to select bounded investigation cohorts.
4. For each potentially recoverable lot, verify identity across `SourceLot`, `CanonicalLot`, `ProcessedLot`, and present sale status, then test promotion with archived primary and live sibling in SQLite/PostgreSQL before production writes.
5. A rollback-ready, verified multi-generation backup plus isolated restore is required before any production migration. Only bounded canaries, no hard deletion.
6. A P14 status/code success is **not** P13–P17 public map acceptance: the strict proposed map remains below the quality coverage threshold and the map is not re-published by these audits.

## Exit criteria

A source-by-source audit of full synchronization coverage and archive cohorts; zero silent status promotion; no archival from failed/incomplete pagination; demonstrated fresh authoritative evidence for each recovered lot; tests green on exact SHA; repeated real Home audit; approval of production canary, backups and rollback. Mark P14 at 100% only after these, and independently verify the owner examples and public map.
