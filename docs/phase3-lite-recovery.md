# Phase 3 Lite production recovery runbook

Phase 3 Lite is intentionally small and optimized for a single home production origin with up to four application users.

## Automated checks

- Production health runs every 30 minutes on the home runner.
- PostgreSQL backups run daily.
- Sunday backup runs include a full restore into an isolated `postgres:17` container with no network.
- The restore drill verifies the Alembic revision plus row-count plausibility for processed lots, GEO snapshots, application users, ingestion runs and map datasets.
- Backup metadata records SHA-256, source/restored schema revisions and restore status.
- GitHub uses one deduplicated open issue per alert class and closes it automatically after recovery.

## Alert meanings

`[Phase 3] Production health alert` covers container health, local API readiness, configured-source freshness/full coverage, expired ingestion lease, map publication state, GEO backlog liveness, disk capacity, recent backup and recent verified restore.

`[Phase 3] Backup/restore alert` means either the dump itself failed or the isolated restore drill could not prove that the backup is usable.

## Manual restore drill

On the home Windows machine from the repository checkout:

```powershell
.\scripts\backup-home-postgres.ps1 -Destination 'C:\ProgramData\BankrotAI\dr-backups' -VerifyRestore -RetainDays 14
```

A successful drill must end with `restore_verification = passed` and matching source/restored schema revisions. The verification container is removed automatically.

## Production recovery rule

Never restore directly over the live database as a first test. First run an isolated restore drill against the chosen dump. Only after the dump passes should production replacement be considered. Preserve the current PostgreSQL volume or take a fresh safety dump before any destructive recovery action.

## Thresholds

- C: drive: critical below 10% free.
- Latest backup: critical when older than 30 hours.
- Latest verified restore drill: critical when older than 192 hours (8 days).
- Source complete snapshot: uses the application freshness contract (36-hour full-coverage threshold).
- GEO backlog: critical when actionable work remains, GEO is not paused, and no completed geocoding batch has been recorded for 24 hours.
