# Phase 3 Lite production recovery runbook

Phase 3 Lite is intentionally small and optimized for a single home production origin with up to four application users.

## Automated checks

- Production health runs every 30 minutes on the home runner.
- The backup workflow checks production daily and creates a new PostgreSQL dump only when the latest verified copy reaches the approximately 48-hour cadence.
- Every replacement backup is restored into an isolated `postgres:17` container with no network before it becomes the retained canonical copy.
- The restore drill verifies the Alembic revision plus row-count plausibility for processed lots, GEO snapshots, application users, ingestion runs and map datasets.
- Backup metadata records SHA-256, source/restored schema revisions and restore status.
- Canonical storage is `D:\BankrotAI\dr-backups`; after a successful replacement, the latest three verified `.dump + .json` generations are retained.
- GitHub uses one deduplicated open issue per alert class and closes it automatically after recovery.

## Alert meanings

`[Phase 3] Production health alert` covers hard production failures: container health, local API readiness, missing configured sources, total source-data unavailability, expired ingestion lease, map publication state, GEO backlog liveness, disk capacity, recent backup and recent verified restore.

`[Phase 3] Source health warning` covers per-source freshness or full-coverage degradation caused by an external source while production itself remains available. Internal ingestion failures such as database-integrity or application exceptions remain critical production-health failures. The warning is deduplicated and closes automatically when the source checks recover.

`[Phase 3] Backup/restore alert` means either the dump itself failed or the isolated restore drill could not prove that the backup is usable.

## Manual restore drill

On the home Windows machine from the repository checkout:

```powershell
.\scripts\backup-home-postgres.ps1 -Destination 'D:\BankrotAI\dr-backups' -VerifyRestore
```

A successful drill must end with `restore_verification = passed` and matching source/restored schema revisions. The verification container is removed automatically.

## Production recovery rule

Never restore directly over the live database as a first test. First run an isolated restore drill against the chosen dump. Only after the dump passes should production replacement be considered. Preserve the current PostgreSQL volume or take a fresh safety dump before any destructive recovery action.

## Thresholds

- C: drive: critical below 10% free.
- Latest backup/restore evidence: critical when older than 60 hours. This matches the approximately 48-hour production cadence with scheduling grace.
- Retained backups must have `restore_verification = passed`; an unverified replacement never evicts the previously verified generations.
- Local retention target: three verified generations on `D:`. Encrypted off-host retention is tracked separately because it requires an owner-approved encryption key and destination.
- Source complete snapshot: uses the application freshness contract (36-hour full-coverage threshold). Individual stale/failed sources are warnings; production becomes critical only when configured source records are missing or no configured source is operational.
- GEO backlog: critical when actionable work remains, GEO is not paused, and no completed geocoding batch has been recorded for 24 hours.
